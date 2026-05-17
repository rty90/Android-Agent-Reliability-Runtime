from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from a2r2.policies.common import (
    history_dicts,
    node_matches_action,
    observation_hash,
    observation_text,
    observation_xml,
    parse_xml_nodes,
)
from a2r2.types import Observation, ProposedAction, RuntimeConfig, RuntimeDecision


LOADING_MARKERS = (
    "loading",
    "please wait",
    "progressbar",
    "progress bar",
    "加载",
    "正在加载",
)

MODAL_MARKERS = (
    "dialog",
    "alert",
    "permission",
    "modal",
    "popupwindow",
    "bottomsheet",
    "blocking overlay",
)

WEBVIEW_BLANK_MARKERS = (
    "webview",
    "web view",
)


class ReadinessPolicy:
    policy_name = "ReadinessPolicy"

    def __init__(self, config: Optional[RuntimeConfig] = None) -> None:
        self.config = config or RuntimeConfig()

    def evaluate(
        self,
        goal: str,
        observation: Observation,
        proposed_action: ProposedAction,
        history: Optional[Iterable[Any]] = None,
    ) -> RuntimeDecision:
        xml_text = observation_xml(observation)
        corpus = observation_text(observation).lower()
        evidence: List[str] = []

        if self._same_hash_repeated(observation, history):
            return self._wait(
                reason="The same UI state has repeated across recent steps.",
                label="loading_loop",
                evidence=["repeated_ui_tree_hash:{0}".format(observation_hash(observation))],
            )

        if not xml_text.strip():
            if observation.screenshot_path:
                return self._wait(
                    reason="Screenshot exists but XML is missing; retry observation before acting.",
                    label="non_ready_action",
                    evidence=["missing_xml", "screenshot_present"],
                )
            return self._wait(
                reason="XML observation is missing or empty.",
                label="non_ready_action",
                evidence=["missing_xml"],
            )

        if len(xml_text.strip()) < 80:
            return self._wait(
                reason="XML observation is too small to trust.",
                label="non_ready_action",
                evidence=["content_poor_xml"],
            )

        if any(marker in corpus for marker in LOADING_MARKERS):
            return self._wait(
                reason="A loading indicator is visible; wait before executing the proposed action.",
                label="non_ready_action",
                evidence=[marker for marker in LOADING_MARKERS if marker in corpus][:5],
            )

        target_problem = self._target_not_actionable(xml_text, proposed_action)
        if target_problem:
            return RuntimeDecision(
                decision="block",
                allowed=False,
                reason="The proposed target is present but not currently actionable.",
                diagnosis_label="non_ready_action",
                evidence=target_problem,
                confidence=0.85,
                policy_name=self.policy_name,
            )

        if any(marker in corpus for marker in MODAL_MARKERS):
            return RuntimeDecision(
                decision="manual_handoff",
                allowed=False,
                reason="A generic modal or overlay appears to be blocking the screen.",
                diagnosis_label="modal_blocker",
                evidence=[marker for marker in MODAL_MARKERS if marker in corpus][:5],
                confidence=0.72,
                policy_name=self.policy_name,
            )

        if self._looks_like_blank_webview(corpus, xml_text):
            return self._wait(
                reason="The UI appears to expose only a content-poor WebView-like surface.",
                label="blank_webview",
                evidence=["webview_content_poor"],
            )

        return RuntimeDecision(
            decision="allow",
            allowed=True,
            reason="The UI appears ready for the proposed action.",
            diagnosis_label=None,
            evidence=[],
            confidence=0.80,
            policy_name=self.policy_name,
        )

    def _wait(self, reason: str, label: str, evidence: List[str]) -> RuntimeDecision:
        return RuntimeDecision(
            decision="wait",
            allowed=False,
            reason=reason,
            diagnosis_label=label,
            evidence=evidence,
            fallback_action=ProposedAction(
                action_type="wait",
                raw={"seconds": self.config.default_wait_seconds},
            ),
            confidence=0.80,
            policy_name=self.policy_name,
        )

    def _same_hash_repeated(self, observation: Observation, history: Optional[Iterable[Any]]) -> bool:
        current_hash = observation_hash(observation)
        if not current_hash:
            return False
        recent_hashes: List[str] = []
        for item in history_dicts(history)[-(self.config.readiness_repeat_threshold - 1) :]:
            after_state = item.get("after_state") if isinstance(item.get("after_state"), dict) else {}
            before_state = item.get("before_state") if isinstance(item.get("before_state"), dict) else {}
            candidate = after_state.get("ui_tree_hash") or before_state.get("ui_tree_hash")
            if candidate:
                recent_hashes.append(str(candidate))
        if len(recent_hashes) < max(0, self.config.readiness_repeat_threshold - 1):
            return False
        return all(item == current_hash for item in recent_hashes)

    def _target_not_actionable(self, xml_text: str, action: ProposedAction) -> List[str]:
        evidence: List[str] = []
        for node in parse_xml_nodes(xml_text):
            if not node_matches_action(node, action):
                continue
            enabled = str(node.get("enabled", "")).lower()
            clickable = str(node.get("clickable", "")).lower()
            if enabled == "false":
                evidence.append("target_enabled_false")
            if action.action_type in {"tap", "click", "input_text", "type_text"} and clickable == "false":
                evidence.append("target_clickable_false")
            if evidence:
                return evidence
        return evidence

    def _looks_like_blank_webview(self, corpus: str, xml_text: str) -> bool:
        if not any(marker in corpus for marker in WEBVIEW_BLANK_MARKERS):
            return False
        nodes = parse_xml_nodes(xml_text)
        visible_text_nodes = [
            node
            for node in nodes
            if str(node.get("text") or node.get("content-desc") or "").strip()
        ]
        return len(visible_text_nodes) <= 2
