"""The web search seam: one Messages call with Anthropic's server-side search.

A separate seam from `Backend.complete`, on purpose. `complete` returns text,
and text is the one thing a search must not be reduced to: what the evidence
ingest stores is WHERE each claim came from - the url, the page's title and
date, the words the model cited - and the model's prose only says which of
those it relied on. `_parse` in backends.py keeps text blocks and drops
everything else, which is right for a question and wrong here.

The search runs on Anthropic's servers (`web_search_20260209`, the version
with dynamic filtering), so the caller needs a route to api.anthropic.com and
nothing else. Results arrive in the same response:

    server_tool_use          the query the model ran
    web_search_tool_result   a LIST of results (url, title, page_age) - or one
                             error OBJECT (error_code), which is not billed
    text                     the model's answer; `citations` on a block point
                             at the results it used, with up to 150 cited chars

`pause_turn` means the server stopped a long turn; it is resumed once by
sending the assistant's content back with the same tools, and only once - a
continuation gets a fresh `max_uses`, so an unbounded loop would be an
unbounded bill. A refusal is an answer (`refused`), never re-routed to another
model (core/llm/backends.py, commitment 6). Nothing here sends temperature,
top_p, top_k or a thinking budget: the current models reject all four.

WHAT A FAILURE COSTS. Each leg (the first call, the continuation) is a billed
server-side turn the moment it starts. So a failed continuation does not
throw away the first leg: the answer comes back `incomplete`, with that leg's
usage, queries and sources, for the caller to record. A leg whose request may
have reached the server with no response seen - a read timeout, a dropped
connection (`backends.possibly_billed`) - may have been billed in full; it is
counted in `lost_legs`, for the caller to price at its worst case. The SDK's
retries are off (`max_retries=0`): a re-sent timeout is a second paid turn,
and whether to pay for one is the next run's decision, made after this one is
recorded. What IS retried here, inside the caller's deadline, is a status the
API returns instead of running the turn (429, 5xx, 529 overloaded): refused
before any work, so free to repeat. A failure of the FIRST leg that cannot
have been billed raises.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from core.llm.tiers import Usage

#: The tool version with dynamic filtering, documented for Opus 5.5 and Sonnet
#: 5.5. Its code execution runs server-side and is not declared separately.
TOOL_TYPE = "web_search_20260209"

#: Continuations of a paused turn. Each gets its own `max_uses`, so this is what
#: bounds the searches one call can run: max_uses x (1 + CONTINUATIONS).
CONTINUATIONS = 1

#: Seconds one leg may take, end to end: the request is not streamed, so this
#: is the whole turn. A three-search turn at effort medium measured well under
#: a minute; a leg that writes all 8,000 output tokens is the slow case.
TIMEOUT = 180.0

#: Retries of a request the API refused before running it (REFUSED_STATUSES).
RETRIES = 2

#: The web search tool's error codes that are distinctive enough to find as
#: words. With dynamic filtering the model runs its searches from code, and a
#: search that fails there reports its code inside a code-execution result, not
#: as a `web_search_tool_result` error object. The first live run (2026-10-28)
#: hit `max_uses_exceeded` that way on both moves, and both were filed as
#: "nothing found". `unavailable` is a code too, but as a word it is page text:
#: it is read only from an `error_code` field.
SEARCH_ERROR_WORDS = (
    "max_uses_exceeded",
    "too_many_requests",
    "query_too_long",
    "invalid_tool_input",
)


@dataclass(frozen=True)
class Source:
    """A page the model cited, as the search returned it."""

    url: str
    title: str
    page_age: str
    cited: tuple[str, ...]


@dataclass
class SearchAnswer:
    model_id: str
    text: str = ""
    sources: list[Source] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    #: Results returned but never cited, url -> (title, page_age): not stored as
    #: evidence (nothing in the answer rests on them), counted on the record.
    uncited: dict[str, tuple[str, str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    stop_reason: str = ""
    refused: bool = False
    refusal: str = ""
    usage: Usage = field(default_factory=lambda: Usage(0, 0))
    request_ids: list[str] = field(default_factory=list)
    #: The turn ended before the model wrote its answer: a continuation that
    #: failed, timed out or did not fit the caller's deadline.
    incomplete: bool = False
    #: Legs that timed out: billed or not, their usage was never seen.
    lost_legs: int = 0
    #: Content block types seen, with counts: on the record, so a turn whose
    #: shape the reader did not expect can be diagnosed from the record alone.
    blocks: dict[str, int] = field(default_factory=dict)


def _add(a: Usage, b: Usage) -> Usage:
    return Usage(
        a.input_tokens + b.input_tokens,
        a.output_tokens + b.output_tokens,
        a.cached_input_tokens + b.cached_input_tokens,
        a.cache_write_tokens + b.cache_write_tokens,
        a.web_search_requests + b.web_search_requests,
    )


def _usage(raw) -> Usage:
    if raw is None:
        return Usage(0, 0)
    tools = getattr(raw, "server_tool_use", None)
    return Usage(
        int(getattr(raw, "input_tokens", 0) or 0),
        int(getattr(raw, "output_tokens", 0) or 0),
        int(getattr(raw, "cache_read_input_tokens", 0) or 0),
        int(getattr(raw, "cache_creation_input_tokens", 0) or 0),
        int(getattr(tools, "web_search_requests", 0) or 0) if tools is not None else 0,
    )


def _field(obj, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class WebSearch:
    """Ask one question with web search on; return what was cited, and the bill.

    `client` is an `anthropic.Anthropic` or anything with `messages.create`;
    built lazily from the environment when None, so importing this module and
    constructing the seam make no network call and need no key."""

    def __init__(
        self,
        model_id: str,
        *,
        max_uses: int = 3,
        effort: str = "medium",
        max_tokens: int = 8000,
        user_location: dict | None = None,
        client=None,
        timeout: float = TIMEOUT,
        sleep=time.sleep,
    ) -> None:
        self.model_id = model_id
        self.max_uses = max_uses
        self.effort = effort
        self.max_tokens = max_tokens
        self.user_location = user_location
        self.timeout = timeout
        self._sleep = sleep
        # Any: the SDK's own client, or a test double with `messages.create`.
        # The request is built from plain dicts, as the API documents it.
        self._client: Any = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from core.llm.backends import anthropic_client

            self._client = anthropic_client(max_retries=0, timeout=self.timeout)
        return self._client

    def tools(self) -> list[dict]:
        tool: dict = {"type": TOOL_TYPE, "name": "web_search", "max_uses": self.max_uses}
        if self.user_location:
            tool["user_location"] = {"type": "approximate", **self.user_location}
        return [tool]

    def search(
        self, system: str, prompt: str, *, deadline: float | None = None, clock=time.monotonic
    ) -> SearchAnswer:
        """`deadline` is a `clock()` reading: a leg is started only when its
        whole timeout fits before it, so the caller's time bound holds."""
        answer = SearchAnswer(self.model_id)
        messages: list[dict] = [{"role": "user", "content": prompt}]
        tools = self.tools()
        for attempt in range(1 + CONTINUATIONS):
            if attempt and deadline is not None and clock() + self.timeout > deadline:
                answer.incomplete = True
                answer.errors.append("continuation not started: the run's deadline")
                break
            try:
                response = self._create(
                    deadline,
                    clock,
                    model=self.model_id,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=messages,
                    tools=tools,
                    output_config={"effort": self.effort},
                )
            except Exception as e:
                from core.llm.backends import possibly_billed

                billed = possibly_billed(e)
                if attempt == 0 and not billed:
                    raise  # never reached the server, or refused there: nothing billed
                answer.lost_legs += int(billed)
                answer.incomplete = True
                leg = "continuation" if attempt else "request"
                answer.errors.append(f"{leg} failed: {type(e).__name__}")
                break
            answer.usage = _add(answer.usage, _usage(getattr(response, "usage", None)))
            rid = getattr(response, "_request_id", None)
            if rid:
                answer.request_ids.append(str(rid))
            self._read(response.content, answer)
            answer.stop_reason = str(getattr(response, "stop_reason", "") or "")
            if answer.stop_reason == "refusal":
                details = getattr(response, "stop_details", None)
                answer.refused = True
                answer.refusal = (
                    f"{_field(details, 'category') or 'unspecified'}: "
                    f"{_field(details, 'explanation') or 'declined'}"
                )
                break
            if answer.stop_reason != "pause_turn" or attempt == CONTINUATIONS:
                break
            # Resume exactly where the server stopped: the assistant's content
            # back, the same tools, and no new user message.
            messages = [*messages, {"role": "assistant", "content": response.content}]
        cited = {s.url for s in answer.sources}
        answer.uncited = {u: v for u, v in answer.uncited.items() if u not in cited}
        return answer

    def _create(self, deadline: float | None, clock, **request):
        """One leg, re-sent only when the API refused it before running it,
        and only while a retry and its whole timeout still fit the deadline."""
        from core.llm.backends import REFUSED_STATUSES, RETRY_AFTER_CAP

        for retry in range(RETRIES + 1):
            try:
                return self.client.messages.create(**request)
            except Exception as e:
                status = getattr(e, "status_code", None)
                if status not in REFUSED_STATUSES or retry == RETRIES:
                    raise
                wait = _retry_after(e) or 2.0 * 2**retry
                wait = min(wait, RETRY_AFTER_CAP)
                if deadline is not None and clock() + wait + self.timeout > deadline:
                    raise
                self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _read(self, blocks, answer: SearchAnswer) -> None:
        # This leg's results. A continuation cites results an EARLIER leg
        # returned - the server resumes where it stopped and does not send
        # them again - so a cited url is looked up in both.
        results: dict[str, tuple[str, str]] = {}
        cited: dict[str, list[str]] = {}
        titles: dict[str, str] = {}
        texts: list[str] = []
        for block in blocks or ():
            kind = _field(block, "type")
            answer.blocks[str(kind)] = answer.blocks.get(str(kind), 0) + 1
            if (
                isinstance(kind, str)
                and kind.endswith("_tool_result")
                and kind != "web_search_tool_result"
            ):
                # A search run from the filtering code fails inside this block.
                answer.errors.extend(_error_codes(_field(block, "content")))
            if kind == "server_tool_use" and _field(block, "name") == "web_search":
                query = _field(_field(block, "input") or {}, "query")
                if query:
                    answer.queries.append(str(query))
            elif kind == "web_search_tool_result":
                content = _field(block, "content")
                if isinstance(content, list):
                    for r in content:
                        url = str(_field(r, "url") or "")
                        if url:
                            results[url] = (
                                str(_field(r, "title") or ""),
                                str(_field(r, "page_age") or ""),
                            )
                else:
                    answer.errors.append(str(_field(content, "error_code") or "error"))
            elif kind == "text":
                texts.append(str(_field(block, "text") or ""))
                for c in _field(block, "citations") or ():
                    if _field(c, "type") != "web_search_result_location":
                        continue
                    url = str(_field(c, "url") or "")
                    snippet = str(_field(c, "cited_text") or "").strip()
                    if url:
                        cited.setdefault(url, [])
                        if snippet and snippet not in cited[url]:
                            cited[url].append(snippet)
                        if _field(c, "title"):
                            titles.setdefault(url, str(_field(c, "title")))
        answer.text = (answer.text + "".join(texts)).strip()
        for url, meta in results.items():
            answer.uncited.setdefault(url, meta)
        known = {s.url: s for s in answer.sources}
        for url, snippets in cited.items():
            title, age = results.get(url) or answer.uncited.get(url) or (titles.get(url, ""), "")
            prior = known.get(url)
            if prior is not None:
                merged = tuple(dict.fromkeys((*prior.cited, *snippets)))
                known[url] = Source(url, prior.title or title, prior.page_age or age, merged)
            else:
                known[url] = Source(url, title or titles.get(url, ""), age, tuple(snippets))
        answer.sources = list(known.values())


def _error_codes(obj, depth: int = 0) -> list[str]:
    """Every search error code inside a tool result: an `error_code` field at
    any depth, or one of SEARCH_ERROR_WORDS in its text (a script's stdout,
    stderr or return value). SDK objects are read through `model_dump`."""
    if depth > 12 or obj is None:
        return []
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        obj = dump()
    if isinstance(obj, str):
        return [w for w in SEARCH_ERROR_WORDS for _ in range(obj.count(w))]
    found: list[str] = []
    if isinstance(obj, dict):
        code = obj.get("error_code")
        if code:
            found.append(str(code))
        for key, value in obj.items():
            if key != "error_code":
                found.extend(_error_codes(value, depth + 1))
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            found.extend(_error_codes(value, depth + 1))
    return found


def _retry_after(e: Exception) -> float | None:
    headers = getattr(getattr(e, "response", None), "headers", None)
    try:
        seconds = float(str(headers.get("retry-after")).strip()) if headers else 0.0
    except ValueError:
        return None
    return seconds if seconds > 0 else None
