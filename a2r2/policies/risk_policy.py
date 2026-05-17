from __future__ import annotations

from typing import Iterable, List, Optional

from a2r2.policies.common import action_text
from a2r2.types import Observation, ProposedAction, RuntimeConfig, RuntimeDecision


HIGH_RISK_KEYWORDS = (
    "delete",
    "remove",
    "reset",
    "uninstall",
    "pay",
    "purchase",
    "submit",
    "send",
    "confirm",
    "\u5220\u9664",
    "\u79fb\u9664",
    "\u91cd\u7f6e",
    "\u5378\u8f7d",
    "\u652f\u4ed8",
    "\u8d2d\u4e70",
    "\u63d0\u4ea4",
    "\u53d1\u9001",
    "\u786e\u8ba4",
)


class RiskPolicy:
    policy_name = "RiskPolicy"

    def __init__(self, config: Optional[RuntimeConfig] = None) -> None:
        self.config = config or RuntimeConfig()

    def evaluate(
        self,
        goal: str,
        observation: Observation,
        proposed_action: ProposedAction,
        readiness_decision: RuntimeDecision,
        history: Optional[Iterable[object]] = None,
    ) -> Optional[RuntimeDecision]:
        risky_markers = self._risky_markers(observation, proposed_action)
        if not risky_markers:
            return None

        evidence = ["high_risk_keyword:{0}".format(marker) for marker in risky_markers[:5]]
        if not readiness_decision.allowed:
            return RuntimeDecision(
                decision="block",
                allowed=False,
                reason="High-risk action proposed while the screen is not ready.",
                diagnosis_label="unsafe_action",
                evidence=evidence
                + ["readiness:{0}".format(readiness_decision.diagnosis_label or readiness_decision.decision)],
                confidence=0.92,
                policy_name=self.policy_name,
            )

        if self._has_confirmation_context(observation, proposed_action):
            return RuntimeDecision(
                decision="allow",
                allowed=True,
                reason="High-risk action has explicit confirmation context.",
                diagnosis_label=None,
                evidence=evidence + ["confirmation_context_present"],
                confidence=0.70,
                policy_name=self.policy_name,
            )

        if self.config.high_risk_requires_handoff:
            return RuntimeDecision(
                decision="manual_handoff",
                allowed=False,
                reason="High-risk action lacks explicit confirmation context.",
                diagnosis_label="unsafe_action",
                evidence=evidence + ["missing_confirmation_context"],
                confidence=0.90,
                policy_name=self.policy_name,
            )

        return RuntimeDecision(
            decision="block",
            allowed=False,
            reason="High-risk action blocked by runtime policy.",
            diagnosis_label="unsafe_action",
            evidence=evidence,
            confidence=0.90,
            policy_name=self.policy_name,
        )

    def _risky_markers(self, observation: Observation, action: ProposedAction) -> List[str]:
        combined = action_text(action)
        return [marker for marker in HIGH_RISK_KEYWORDS if marker in combined]

    def _has_confirmation_context(self, observation: Observation, action: ProposedAction) -> bool:
        raw = action.raw if isinstance(action.raw, dict) else {}
        metadata = observation.metadata or {}
        return bool(
            raw.get("confirmed")
            or raw.get("confirmed_by_user")
            or raw.get("confirmation_context")
            or metadata.get("risk_confirmed")
            or metadata.get("confirmation_context")
        )
