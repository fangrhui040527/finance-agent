"""Default policy set. All five rails covered."""

from __future__ import annotations

from datetime import timedelta

from core.guardrails.policy import (
    AdviceLanguagePolicy,
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
    Action,
    Decision,
)

# docs/02 section 4
CORPUS_SLA = {
    "price_bars_intraday": timedelta(minutes=20),
    "price_bars_eod": timedelta(days=1),
    "fundamental_facts": timedelta(hours=24),
    "kb_news": timedelta(minutes=30),
    "kb_filings": timedelta(hours=24),
    "macro_series": timedelta(days=1),
}


class InjectionScanPolicy(PolicyRule):
    """Input rail. Retrieved text is data, never instructions."""

    name = "injection_scan"
    rails = (Rail.INPUT,)
    MARKERS = ("ignore previous instructions", "disregard the system prompt",
               "you are now", "reveal your system prompt")

    def evaluate(self, action: Action) -> PolicyResult | None:
        text = str(action.payload.get("text", "")).lower()
        for m in self.MARKERS:
            if m in text:
                return PolicyResult(Decision.DENY, self.name, f"injection marker: {m!r}")
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
