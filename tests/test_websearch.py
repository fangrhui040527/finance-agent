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
