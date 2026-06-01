from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

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

        if self._screen_independent_action(proposed_action):
            return RuntimeDecision(
                decision="allow",
                allowed=True,
                reason="The proposed action does not depend on the current UI being actionable.",
                diagnosis_label=None,
                evidence=["screen_independent_action:{0}".format(proposed_action.action_type)],
                confidence=0.82,
                policy_name=self.policy_name,
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

        target_missing = self._target_missing_from_facts(observation, proposed_action)
        if target_missing:
            return RuntimeDecision(
                decision="block",
                allowed=False,
                reason="The proposed target is not present in the current actionable UI facts.",
                diagnosis_label="target_missing",
                evidence=target_missing,
                confidence=0.78,
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

    def _screen_independent_action(self, action: ProposedAction) -> bool:
        return str(action.action_type or "").lower() in {"open_app", "wait"}

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

    def _target_missing_from_facts(self, observation: Observation, action: ProposedAction) -> List[str]:
        if not self._requires_named_target(action):
            return []
        target_text = str(action.target_text or "").strip()
        target_id = str(action.target_resource_id or "").strip()
        if not target_text and not target_id:
            return []

        metadata = observation.metadata or {}
        target_facts = metadata.get("possible_targets")
        if not isinstance(target_facts, list):
            return []
        if self._target_facts_incomplete(metadata, target_facts):
            return []

        if not target_facts:
            return ["target_facts_empty", "missing_target:{0}".format(target_text or target_id)]

        for target in target_facts:
            if isinstance(target, Mapping) and self._target_fact_matches(target, target_text, target_id):
                return []
        return [
            "target_not_in_possible_targets",
            "missing_target:{0}".format(target_text or target_id),
            "possible_target_count:{0}".format(len(target_facts)),
        ]

    def _requires_named_target(self, action: ProposedAction) -> bool:
        action_type = str(action.action_type or "").lower()
        if action_type in {"tap", "click"}:
            return bool(action.target_text or action.target_resource_id or action.x is None or action.y is None)
        return bool(action.target_resource_id and action_type in {"type_text", "input_text"})

    def _target_fact_matches(self, target: Mapping[str, Any], target_text: str, target_id: str) -> bool:
        haystack = " ".join(
            str(target.get(key) or "").strip().lower()
            for key in ("label", "text", "content_desc", "resource_id", "target_id", "hint")
        )
        wanted_id = target_id.lower()
        wanted_text = target_text.lower()
        if wanted_id and wanted_id in haystack:
            return True
        if wanted_text and wanted_text in haystack:
            return True
        return False

    def _target_facts_incomplete(self, metadata: Mapping[str, Any], target_facts: List[Any]) -> bool:
        if bool(metadata.get("possible_targets_truncated")):
            return True
        total = metadata.get("possible_target_total_count", metadata.get("possible_target_count"))
        if isinstance(total, int) and total > len(target_facts):
            return True
        if isinstance(total, int) and total >= 50 and len(target_facts) >= 50:
            return True
        # Older traces and summaries had only the first 50 targets and no
        # explicit truncation bit. Treat a full 50-item sample as insufficient
        # evidence for a strong missing-target conclusion.
        if len(target_facts) >= 50 and "possible_targets_truncated" not in metadata:
            return True
        return False

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
