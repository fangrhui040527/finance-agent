"""Default policy set. All five rails covered."""

from __future__ import annotations

import re
from datetime import timedelta

from core.guardrails.policy import (
    Action,
    AdviceLanguagePolicy,
    Decision,
    LicenceFilterPolicy,
    NoExecutionPolicy,
    PolicyEngine,
    PolicyResult,
    PolicyRule,
    Rail,
    RateLimitPolicy,
    StalenessPolicy,
    TenantIsolationPolicy,
    ToolAllowlistPolicy,
)

# docs/02 section 4
CORPUS_SLA = {
    "price_bars_intraday": timedelta(minutes=20),
    "price_bars_eod": timedelta(days=1),
    "fundamental_facts": timedelta(hours=24),
    # The collector runs three times a day (Bursa close, US pre-open, US
    # close), not every fifteen minutes as docs/02 imagined for a streaming
    # GDELT. An SLA of 30 minutes on a corpus filled thrice daily marks every
    # article stale on arrival; 12 hours is one collection interval plus
    # slack, and 3x that (the DENY line) is a missed day, which is the fault
    # this policy exists to surface.
    "kb_news": timedelta(hours=12),
    "kb_filings": timedelta(hours=24),
    "macro_series": timedelta(days=1),
}


#: Chat-template control tokens that must never survive inside retrieved text.
#: They are not instructions to match on - their mere PRESENCE is the attack,
#: because a downstream template would let them impersonate a role boundary.
SPECIAL_TOKENS = ("<|im_start|>", "<|im_end|>", "[INST]", "[/INST]", "<<SYS>>", "<|system|>")


def neutralize_special_tokens(text: str) -> str:
    """Break template control tokens so quoted evidence stays quotable.

    DENY is the right answer at the rail; this is for the one path that must
    still SHOW hostile text to a human (a trace, a red-team report) without
    re-arming it."""
    for tok in SPECIAL_TOKENS:
        text = text.replace(tok, tok.replace("|", "\u2758").replace("[", "(").replace("]", ")"))
    return text


class InjectionScanPolicy(PolicyRule):
    """Input AND retrieval rails. Text is data, never instructions.

    Two layers: substring markers for the classic phrasings, and regex rules
    per category so a rewording ("kindly set aside all prior guidance") still
    trips the same wire. Detection DENIES; nothing here rewrites and forwards.

    The INDIRECT vector is the one that matters here, and for a long time this
    rule could not see it. A question typed by the person was scanned on the
    input rail, but the articles retrieved to answer it were not: agents guarded
    retrieval as `_guard_tool("retrieve", {"corpus": corpus})`, a payload
    carrying the corpus NAME and no text, so this rule read an empty string and
    allowed. Anyone able to get a sentence into a collected news story - which
    on a public wire is anyone - was writing straight into the model's context.
    knowledge/retrieval/pipeline.quarantine now runs every returned chunk past
    this rule on Rail.RETRIEVAL, and OWASP LLM01-indirect is the ragqa check
    that fails if that call is ever removed.
    """

    name = "injection_scan"
    rails = (Rail.INPUT, Rail.RETRIEVAL)
    MARKERS = (
        "ignore previous instructions",
        "disregard the system prompt",
        "you are now",
        "reveal your system prompt",
    )
    RULES: tuple[tuple[str, str], ...] = (
        (
            "override",
            r"(?:ignore|disregard|forget|set aside)\s+(?:all\s+)?(?:previous|prior|earlier|above)\s+(?:instructions|guidance|rules|prompts)",
        ),
        ("override", r"(?:new|updated)\s+system\s+prompt\s*:"),
        ("impersonation", r"you\s+are\s+now\s+(?:a|an|the|in)\b"),
        (
            "impersonation",
            r"^\s*system\s*:",
        ),
        (
            "exfiltration",
            r"(?:reveal|print|repeat|show)\s+(?:your|the)\s+(?:system\s+prompt|instructions|api[\s_-]?key|credentials)",
        ),
        ("exfiltration", r"api[\s_-]?key\s*[:=]"),
    )

    def evaluate(self, action: Action) -> PolicyResult | None:
        raw = str(action.payload.get("text", ""))
        text = raw.lower()
        for m in self.MARKERS:
            if m in text:
                return PolicyResult(Decision.DENY, self.name, f"injection marker: {m!r}")
        for tok in SPECIAL_TOKENS:
            if tok.lower() in text:
                return PolicyResult(Decision.DENY, self.name, f"template control token: {tok!r}")
        for category, pattern in self.RULES:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                return PolicyResult(
                    Decision.DENY, self.name, f"injection pattern ({category}): {pattern!r}"
                )
        return None


class DisclaimerPolicy(PolicyRule):
    """Publication rail. docs/05: every output ships a provenance block."""

    name = "disclaimer"
    rails = (Rail.PUBLICATION,)

    def evaluate(self, action: Action) -> PolicyResult | None:
        if action.rail is Rail.PUBLICATION and not action.payload.get("disclaimer"):
            return PolicyResult(
                Decision.DENY, self.name, "output must carry a provenance and disclaimer block"
            )
        return None


def default_engine(tool_allowlist: dict[str, set[str]] | None = None) -> PolicyEngine:
    return PolicyEngine(
        [
            InjectionScanPolicy(),
            TenantIsolationPolicy(),
            LicenceFilterPolicy(),
            StalenessPolicy(CORPUS_SLA),
            NoExecutionPolicy(),
            ToolAllowlistPolicy(tool_allowlist or {}),
            RateLimitPolicy(max_calls=1000, window_seconds=86400),
            AdviceLanguagePolicy(),
            DisclaimerPolicy(),
        ]
    )
