from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from a2r2.policies.common import (
    action_fingerprint,
    file_hash,
    history_dicts,
    observation_hash,
    observation_text,
    observation_xml,
    stable_hash,
)
from a2r2.types import Observation, ProgressVerification, ProposedAction, RuntimeConfig


class ProgressPolicy:
    policy_name = "ProgressPolicy"

    def __init__(self, config: Optional[RuntimeConfig] = None) -> None:
        self.config = config or RuntimeConfig()

    def evaluate(
        self,
        goal: str,
        before_observation: Observation,
        action: ProposedAction,
        after_observation: Observation,
        history: Optional[Iterable[Any]] = None,
    ) -> ProgressVerification:
        # When the harness could not observe the post-action state (e.g. the
        # final step of an imported trajectory has no successor observation),
        # change signals are unknowable: never conclude no_progress/stuck from a
        # fabricated self-comparison. false_success still applies (it judges the
        # claim against the goal marker, not against UI change).
        after_missing = bool((after_observation.metadata or {}).get("after_observation_missing"))

        before_ui_hash = observation_hash(before_observation)
        after_ui_hash = observation_hash(after_observation)
        ui_changed: Optional[bool] = None
        if before_ui_hash and after_ui_hash and not after_missing:
            ui_changed = before_ui_hash != after_ui_hash

        before_xml_hash = self._xml_hash(before_observation)
        after_xml_hash = self._xml_hash(after_observation)
        xml_changed: Optional[bool] = None
        if before_xml_hash and after_xml_hash and not after_missing:
            xml_changed = before_xml_hash != after_xml_hash

        before_screenshot_hash = file_hash(before_observation.screenshot_path)
        after_screenshot_hash = file_hash(after_observation.screenshot_path)
        screenshot_changed: Optional[bool] = None
        if before_screenshot_hash and after_screenshot_hash and not after_missing:
            screenshot_changed = before_screenshot_hash != after_screenshot_hash

        changed_signals = [
            value for value in (ui_changed, xml_changed, screenshot_changed) if value is not None
        ]
        progress_made = any(changed_signals)
        evidence: List[str] = []
        if after_missing:
            evidence.append("after_observation_missing")
        if ui_changed is True:
            evidence.append("ui_tree_hash_changed")
        if xml_changed is True:
            evidence.append("xml_changed")
        if screenshot_changed is True:
            evidence.append("screenshot_changed")
        if not progress_made and not after_missing:
            evidence.append("no_observable_change")

        agent_claimed_success = self._agent_claimed_success(action)
        false_success_candidate = bool(
            agent_claimed_success and not progress_made and not self._goal_marker_present(goal, after_observation)
        )
        no_progress_expected = self._no_progress_expected(action)
        if no_progress_expected and not progress_made:
            evidence.append("no_progress_expected_for:{0}".format(action.action_type))

        diagnosis_label: Optional[str] = None
        if false_success_candidate:
            diagnosis_label = "false_success"
            evidence.append("agent_claimed_done_without_observed_goal_marker")
        elif (
            not no_progress_expected
            and xml_changed is not True
            and self._same_screen_action_repeated(before_observation, action, history)
        ):
            # The agent repeats the same action on the same screen identity
            # while the structured UI does not change. This catches loops even
            # when pixel/hash signals are noisy (e.g. per-step screenshot files).
            # Repetition evidence comes from already-observed history, so this
            # holds even when the post-action observation is missing (a loop's
            # n-th repeat is often the episode's final recorded step).
            diagnosis_label = "stuck_loop"
            evidence.append("same_action_same_screen_repeated")
        elif (
            self._repeated_no_progress(action, history)
            and not progress_made
            and not no_progress_expected
            and not after_missing
        ):
            diagnosis_label = "stuck_loop"
            evidence.append("same_action_repeated_without_progress")
        elif not progress_made and not no_progress_expected and not after_missing:
            diagnosis_label = "no_progress"

        return ProgressVerification(
            progress_made=progress_made,
            ui_changed=ui_changed,
            xml_changed=xml_changed,
            screenshot_changed=screenshot_changed,
            agent_claimed_success=agent_claimed_success,
            false_success_candidate=false_success_candidate,
            diagnosis_label=diagnosis_label,
            evidence=evidence,
        )

    def _xml_hash(self, observation: Observation) -> Optional[str]:
        xml_text = observation_xml(observation)
        if xml_text:
            return stable_hash(xml_text)
        return None

    def _agent_claimed_success(self, action: ProposedAction) -> bool:
        raw = action.raw or {}
        return bool(
            action.action_type.lower() in {"done", "finish", "complete", "success"}
            or raw.get("agent_claimed_success")
            or raw.get("done")
        )

    def _no_progress_expected(self, action: ProposedAction) -> bool:
        # Control / terminal actions are not expected to advance the UI, so a
        # lack of observable change must not be read as `no_progress`. A terminal
        # claim that is actually unfounded is still caught earlier as
        # `false_success` (checked before this gate).
        return str(action.action_type or "").lower() in {
            "confirm",
            "wait",
            "done",
            "complete",
            "finish",
            "success",
            "terminate",
            "answer",
            "noop",
        }

    def _goal_marker_present(self, goal: str, observation: Observation) -> bool:
        metadata = observation.metadata or {}
        if metadata.get("goal_satisfied") is True or metadata.get("goal_marker_present") is True:
            return True
        if metadata.get("goal_satisfied") is False or metadata.get("goal_marker_present") is False:
            return False
        markers = metadata.get("goal_markers")
        if not isinstance(markers, (list, tuple, set)):
            return False
        corpus = observation_text(observation).lower()
        return any(str(marker).strip().lower() in corpus for marker in markers if str(marker).strip())

    @staticmethod
    def _surface_action_key(action_type: Any, x: Any, y: Any, target_text: Any, target_id: Any) -> tuple:
        # Reduced action identity for repetition checks: raw is excluded on
        # purpose because free-text fields (agent thoughts) differ every step.
        return (
            str(action_type or "").lower(),
            x,
            y,
            str(target_text or ""),
            str(target_id or ""),
        )

    def _same_screen_action_repeated(
        self, before: Observation, action: ProposedAction, history: Optional[Iterable[Any]]
    ) -> bool:
        screen_key = (str(before.package or ""), str(before.activity or ""))
        if not screen_key[0] and not screen_key[1]:
            return False
        current = self._surface_action_key(
            action.action_type, action.x, action.y, action.target_text, action.target_resource_id
        )
        repeats = 1
        for item in reversed(history_dicts(history)):
            pa = item.get("proposed_action") if isinstance(item.get("proposed_action"), dict) else {}
            bs = item.get("before_state") if isinstance(item.get("before_state"), dict) else {}
            if not pa:
                break
            prior = self._surface_action_key(
                pa.get("action_type"), pa.get("x"), pa.get("y"), pa.get("target_text"), pa.get("target_resource_id")
            )
            prior_key = (str(bs.get("package") or ""), str(bs.get("activity") or ""))
            if prior != current or prior_key != screen_key:
                break
            repeats += 1
            if repeats >= self.config.progress_repeat_threshold:
                return True
        return False

    def _repeated_no_progress(self, action: ProposedAction, history: Optional[Iterable[Any]]) -> bool:
        current = action_fingerprint(action)
        repeated_no_progress = 1
        for item in reversed(history_dicts(history)):
            proposed_action = item.get("proposed_action") if isinstance(item.get("proposed_action"), dict) else {}
            verification = item.get("progress_verification") if isinstance(item.get("progress_verification"), dict) else {}
            if not proposed_action:
                continue
            prior = ProposedAction(
                action_type=str(proposed_action.get("action_type") or ""),
                x=proposed_action.get("x"),
                y=proposed_action.get("y"),
                target_text=proposed_action.get("target_text"),
                target_resource_id=proposed_action.get("target_resource_id"),
                raw=proposed_action.get("raw"),
            )
            if action_fingerprint(prior) != current:
                break
            if verification.get("progress_made") is False:
                repeated_no_progress += 1
            if repeated_no_progress >= self.config.progress_repeat_threshold:
                return True
        return False
