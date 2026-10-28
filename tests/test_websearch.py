"""The web search seam: what it sends, and what it keeps from what comes back.

Offline: the client is a double whose responses have the shapes the SDK
returns (server_tool_use, web_search_tool_result as a list or an error object,
text with web_search_result_location citations, pause_turn, refusal).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace as NS

from core.llm.tiers import Tier, cost_usd
from core.llm.websearch import TOOL_TYPE, WebSearch


def _usage(i=1000, o=200, searches=1, read=0):
    return NS(
        input_tokens=i,
        output_tokens=o,
        cache_read_input_tokens=read,
        cache_creation_input_tokens=0,
        server_tool_use=NS(web_search_requests=searches, web_fetch_requests=0),
    )


def _response(content, stop="end_turn", usage=None, details=None):
    return NS(content=content, stop_reason=stop, usage=usage or _usage(), stop_details=details)


SEARCH = {
    "type": "server_tool_use",
    "id": "srvtoolu_1",
    "name": "web_search",
    "input": {"query": "Press Metal shares fall October 16 2026"},
}
RESULTS = {
    "type": "web_search_tool_result",
    "tool_use_id": "srvtoolu_1",
    "content": [
        {
            "type": "web_search_result",
            "url": "https://www.theborneopost.com/2026/10/19/rhb-press-metal/",
            "title": "RHB: Press Metal sell-off a buying opportunity",
            "page_age": "October 19, 2026",
            "encrypted_content": "x",
        },
        {
            "type": "web_search_result",
            "url": "https://example.com/unrelated",
            "title": "Unrelated",
            "page_age": "",
            "encrypted_content": "y",
        },
    ],
}
ANSWER = {
    "type": "text",
    "text": "RHB attributed the fall to lower aluminium prices.",
    "citations": [
        {
            "type": "web_search_result_location",
            "url": "https://www.theborneopost.com/2026/10/19/rhb-press-metal/",
            "title": "RHB: Press Metal sell-off a buying opportunity",
            "cited_text": "the sell-off was driven by falling aluminium prices and higher carbon anode costs",
            "encrypted_index": "z",
        }
    ],
}


class Client:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def test_the_request_is_one_search_tool_and_no_parameter_the_current_models_reject():
    client = Client(_response([SEARCH, RESULTS, ANSWER]))
    seam = WebSearch(
        "claude-opus-5-5",
        max_uses=3,
        effort="medium",
        client=client,
        user_location={"country": "MY", "timezone": "Asia/Kuala_Lumpur"},
    )
    seam.search("system", "why did it move")
    sent = client.calls[0]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["tools"] == [
        {
            "type": TOOL_TYPE,
            "name": "web_search",
            "max_uses": 3,
            "user_location": {
                "type": "approximate",
                "country": "MY",
                "timezone": "Asia/Kuala_Lumpur",
            },
        }
    ]
    assert sent["output_config"] == {"effort": "medium"}
    for banned in ("temperature", "top_p", "top_k", "thinking", "tool_choice", "fallbacks"):
        assert banned not in sent, banned


def test_what_is_kept_is_what_was_cited_with_its_url_title_date_and_words():
    seam = WebSearch("claude-opus-5-5", client=Client(_response([SEARCH, RESULTS, ANSWER])))
    a = seam.search("s", "p")
    assert a.queries == ["Press Metal shares fall October 16 2026"]
    assert [s.url for s in a.sources] == [
        "https://www.theborneopost.com/2026/10/19/rhb-press-metal/"
    ]
    src = a.sources[0]
    assert src.page_age == "October 19, 2026"
    assert "falling aluminium prices" in src.cited[0]
    assert list(a.uncited) == ["https://example.com/unrelated"], "found, never cited: not evidence"
    assert a.usage.web_search_requests == 1
    assert a.text.startswith("RHB attributed")


def test_a_search_error_is_an_object_not_a_list_and_is_kept_as_an_error():
    failed = {
        "type": "web_search_tool_result",
        "tool_use_id": "s",
        "content": {"type": "web_search_tool_result_error", "error_code": "too_many_requests"},
    }
    a = WebSearch(
        "m",
        client=Client(_response([SEARCH, failed, {"type": "text", "text": "I could not search."}])),
    ).search("s", "p")
    assert a.errors == ["too_many_requests"] and a.sources == []


def test_a_paused_turn_is_resumed_once_with_the_same_tools_and_the_bill_is_summed():
    first = _response([SEARCH, RESULTS], stop="pause_turn", usage=_usage(1000, 100, 1))
    second = _response([ANSWER], usage=_usage(3000, 300, 2))
    client = Client(first, second)
    a = WebSearch("claude-opus-5-5", client=client).search("s", "p")
    assert len(client.calls) == 2
    resumed = client.calls[1]
    assert resumed["tools"] == client.calls[0]["tools"]
    assert resumed["messages"][-1] == {"role": "assistant", "content": first.content}
    assert len(resumed["messages"]) == 2, "no extra user message on a resume"
    assert a.usage.input_tokens == 4000 and a.usage.web_search_requests == 3
    assert [s.url for s in a.sources] == [
        "https://www.theborneopost.com/2026/10/19/rhb-press-metal/"
    ]
    # The continuation cites what the FIRST leg returned: its date survives.
    assert a.sources[0].page_age == "October 19, 2026"


def test_a_turn_that_pauses_again_is_not_resumed_a_second_time():
    paused = [_response([SEARCH], stop="pause_turn") for _ in range(3)]
    client = Client(*paused)
    a = WebSearch("m", client=client).search("s", "p")
    assert len(client.calls) == 2 and a.stop_reason == "pause_turn"


def test_a_refusal_is_an_answer_and_is_not_retried():
    client = Client(
        _response([], stop="refusal", details=NS(category="cyber", explanation="declined"))
    )
    a = WebSearch("m", client=client).search("s", "p")
    assert a.refused and a.refusal == "cyber: declined" and len(client.calls) == 1


def test_a_search_is_billed_per_search_on_top_of_the_tokens():
    a = WebSearch(
        "claude-opus-5-5",
        client=Client(_response([SEARCH, RESULTS, ANSWER], usage=_usage(10_000, 1000, 3))),
    ).search("s", "p")
    # 10k in at $4/M + 1k out at $20/M + 3 searches at $0.01
    assert cost_usd(Tier.REASON, a.usage, model_id="claude-opus-5-5") == Decimal("0.09")


def test_a_failed_continuation_keeps_the_first_legs_bill_and_finds():
    first = _response([SEARCH, RESULTS], stop="pause_turn", usage=_usage(1000, 100, 1))

    class Failing(Client):
        def create(self, **kw):
            self.calls.append(kw)
            if len(self.calls) == 2:
                raise RuntimeError("overloaded")
            return self.responses.pop(0)

    a = WebSearch("claude-opus-5-5", client=Failing(first)).search("s", "p")
    assert a.incomplete and a.usage.web_search_requests == 1 and a.queries
    assert a.errors == ["continuation failed: RuntimeError"] and a.lost_legs == 0


def test_a_first_leg_that_fails_before_any_work_raises():
    import pytest

    class Down(Client):
        def create(self, **kw):
            raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError):
        WebSearch("m", client=Down()).search("s", "p")


def test_a_leg_that_times_out_is_counted_as_lost_not_free():
    import anthropic
    import httpx

    class Slow(Client):
        def create(self, **kw):
            raise anthropic.APITimeoutError(request=httpx.Request("POST", "https://x"))

    a = WebSearch("m", client=Slow()).search("s", "p")
    assert a.lost_legs == 1 and a.incomplete and a.usage.input_tokens == 0


def test_a_continuation_that_cannot_finish_before_the_deadline_is_not_started():
    first = _response([SEARCH, RESULTS], stop="pause_turn")
    client = Client(first, _response([ANSWER]))
    a = WebSearch("m", client=client, timeout=180.0).search(
        "s", "p", deadline=100.0, clock=lambda: 0.0
    )
    assert len(client.calls) == 1 and a.incomplete
    assert a.errors == ["continuation not started: the run's deadline"]


def test_the_client_names_the_workspace_and_never_retries(monkeypatch):
    """An identity-linked key gets a 400 without the header (backends.py); and
    an SDK retry of a timed-out search is a second paid turn."""
    import anthropic

    seen: dict = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(anthropic, "Anthropic", spy)
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")
    assert WebSearch("m").client is not None
    assert seen["default_headers"] == {"anthropic-workspace-id": "wrkspc_test"}
    assert seen["max_retries"] == 0


def _sdk_seam(handler):
    """A WebSearch on the real SDK client, over a transport that fails as told."""
    import anthropic
    from anthropic._base_client import httpx2

    client = anthropic.Anthropic(
        api_key="test-not-a-key",
        max_retries=0,
        timeout=5,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return WebSearch("claude-opus-5-5", client=client), httpx2


def test_a_connection_dropped_after_the_request_went_out_is_counted_as_lost():
    """The server may have run the turn and billed it; the SDK raises
    APIConnectionError, not a timeout, and that is no proof of 'nothing billed'."""
    from anthropic._base_client import httpx2

    def dropped(req):
        req.read()
        raise httpx2.RemoteProtocolError("Server disconnected without sending a response")

    seam, _ = _sdk_seam(dropped)
    a = seam.search("s", "p")
    assert a.lost_legs == 1 and a.incomplete


def test_a_request_that_never_reached_the_server_raises_unbilled():
    """Refused at connect, by a proxy that would not CONNECT, or for a scheme
    the client cannot speak: nothing reached the API, so nothing is charged
    and the next run may try."""
    import anthropic
    import pytest
    from anthropic._base_client import httpx2

    for unsent in (httpx2.ConnectError, httpx2.ProxyError, httpx2.UnsupportedProtocol):

        def refused(req, unsent=unsent):
            raise unsent("not sent")

        seam, _ = _sdk_seam(refused)
        with pytest.raises(anthropic.APIConnectionError):
            seam.search("s", "p")


class _Refused(Exception):
    """What the SDK raises for a status the API answers instead of a turn."""

    def __init__(self, status):
        super().__init__(f"{status}")
        self.status_code = status


def test_a_continuation_the_api_refused_before_running_is_retried_and_completes():
    first = _response([SEARCH, RESULTS], stop="pause_turn", usage=_usage(1000, 100, 1))
    client = Client(first, _Refused(529), _response([ANSWER]))

    def create(**kw):
        client.calls.append(kw)
        r = client.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    client.create = create  # type: ignore[method-assign]
    slept: list[float] = []
    a = WebSearch("claude-opus-5-5", client=client, sleep=slept.append).search("s", "p")
    assert not a.incomplete and a.sources and slept == [2.0]
    assert len(client.calls) == 3


def test_a_refused_request_is_not_retried_past_the_deadline_or_forever():
    import pytest

    first = _response([SEARCH, RESULTS], stop="pause_turn")
    client = Client(first, _Refused(529))

    def create(**kw):
        client.calls.append(kw)
        r = client.responses.pop(0) if client.responses else _Refused(529)
        if isinstance(r, Exception):
            raise r
        return r

    client.create = create  # type: ignore[method-assign]
    late = WebSearch("m", client=client, timeout=180.0, sleep=lambda s: None).search(
        "s", "p", deadline=181.0, clock=lambda: 0.0
    )
    assert late.incomplete and len(client.calls) == 2, "no retry that cannot finish in time"

    always = Client()
    always.create = lambda **kw: (_ for _ in ()).throw(_Refused(503))  # type: ignore[method-assign]
    with pytest.raises(_Refused):
        WebSearch("m", client=always, sleep=lambda s: None).search("s", "p")


def test_a_search_that_fails_inside_the_filtering_code_is_kept_as_an_error():
    """Dynamic filtering runs the searches from code, so a failed search
    reports its code inside a code-execution result. The first live run hit
    `max_uses_exceeded` that way and both moves read "nothing found"."""

    class Dumped:  # an SDK object: read through model_dump
        def model_dump(self):
            return {
                "type": "bash_code_execution_tool_result_error",
                "error_code": "too_many_requests",
            }

    script = {
        "type": "code_execution_tool_result",
        "tool_use_id": "c1",
        "content": {
            "type": "code_execution_result",
            "stdout": 'query 2: {"error_code": "max_uses_exceeded"}\nquery 3: max_uses_exceeded\n'
            "page text: the service was unavailable for an hour\n",
            "stderr": "",
            "return_code": 0,
        },
    }
    failed = {"type": "bash_code_execution_tool_result", "tool_use_id": "c2", "content": Dumped()}
    a = WebSearch(
        "m",
        client=Client(
            _response([SEARCH, RESULTS, script, failed, {"type": "text", "text": "Nothing."}])
        ),
    ).search("s", "p")
    assert a.errors == ["max_uses_exceeded", "max_uses_exceeded", "too_many_requests"]
    assert "unavailable" not in a.errors, "a word in page text is not an error code"
    assert a.blocks == {
        "server_tool_use": 1,
        "web_search_tool_result": 1,
        "code_execution_tool_result": 1,
        "bash_code_execution_tool_result": 1,
        "text": 1,
    }


def test_an_error_code_field_is_read_once_not_again_as_text():
    nested = {
        "type": "code_execution_tool_result",
        "content": {"type": "x", "error_code": "max_uses_exceeded"},
    }
    a = WebSearch("m", client=Client(_response([nested]))).search("s", "p")
    assert a.errors == ["max_uses_exceeded"]
