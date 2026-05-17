from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, Optional

from a2r2.policies.common import allow_decision
from a2r2.policies.progress_policy import ProgressPolicy
from a2r2.policies.readiness_policy import ReadinessPolicy
from a2r2.policies.risk_policy import RiskPolicy
from a2r2.recorder.trace_recorder import TraceRecorder
from a2r2.types import (
    EpisodeSummary,
    Observation,
    ProgressVerification,
    ProposedAction,
    RuntimeConfig,
    RuntimeDecision,
    StepTrace,
    utc_now,
)


class ReliabilityRuntime:
    """Black-box reliability middleware around an external Android GUI agent."""

    def __init__(
        self,
        config: Optional[RuntimeConfig] = None,
        recorder: Optional[TraceRecorder] = None,
        readiness_policy: Optional[ReadinessPolicy] = None,
        risk_policy: Optional[RiskPolicy] = None,
        progress_policy: Optional[ProgressPolicy] = None,
    ) -> None:
        self.config = config or RuntimeConfig()
        self.episode_id = self.config.episode_id or "episode_{0}".format(uuid.uuid4().hex[:12])
        self.trace_id = "trace_{0}".format(uuid.uuid4().hex[:12])
        self.recorder = recorder or TraceRecorder(
            root_dir=self.config.traces_root,
            episode_id=self.episode_id,
        )
        self.readiness_policy = readiness_policy or ReadinessPolicy(self.config)
        self.risk_policy = risk_policy or RiskPolicy(self.config)
        self.progress_policy = progress_policy or ProgressPolicy(self.config)
        self._step_index = 0
        self._last_goal = ""

    def check_before_action(
        self,
        goal: str,
        observation: Observation,
        proposed_action: ProposedAction,
        history: Optional[Iterable[Any]] = None,
    ) -> RuntimeDecision:
        self._last_goal = goal
        readiness = self.readiness_policy.evaluate(
            goal=goal,
            observation=observation,
            proposed_action=proposed_action,
            history=history,
        )
        risk = self.risk_policy.evaluate(
            goal=goal,
            observation=observation,
            proposed_action=proposed_action,
            readiness_decision=readiness,
            history=history,
        )
        if risk is not None:
            return risk
        if not readiness.allowed:
            return readiness
        return allow_decision()

    def verify_after_action(
        self,
        goal: str,
        before_observation: Observation,
        action: ProposedAction,
        after_observation: Observation,
        history: Optional[Iterable[Any]] = None,
    ) -> ProgressVerification:
        self._last_goal = goal
        return self.progress_policy.evaluate(
            goal=goal,
            before_observation=before_observation,
            action=action,
            after_observation=after_observation,
            history=history,
        )

    def record_step(
        self,
        goal: str,
        before_observation: Observation,
        proposed_action: ProposedAction,
        decision: RuntimeDecision,
        after_observation: Observation,
        verification: ProgressVerification,
        history: Optional[Iterable[Any]] = None,
    ) -> Dict[str, Any]:
        self._last_goal = goal
        self._step_index += 1
        diagnosis_label = (
            verification.diagnosis_label
            or decision.diagnosis_label
            or ("blocked_action" if not decision.allowed else None)
        )
        step = StepTrace(
            trace_id=self.trace_id,
            episode_id=self.episode_id,
            step_index=self._step_index,
            goal=goal,
            agent_meta=self._agent_meta(),
            before_state=before_observation.to_dict(),
            proposed_action=proposed_action.to_dict(),
            runtime_decision=decision.to_dict(),
            after_state=after_observation.to_dict(),
            progress_verification=verification.to_dict(),
            diagnosis={
                "label": diagnosis_label,
                "decision_label": decision.diagnosis_label,
                "progress_label": verification.diagnosis_label,
            },
            timestamps={
                "before_observation": before_observation.timestamp,
                "after_observation": after_observation.timestamp,
                "recorded_at": utc_now(),
            },
            artifacts_dir=str(self.recorder.artifacts_dir),
        )
        return self.recorder.record_step(step)

    def write_episode_summary(
        self,
        goal: Optional[str] = None,
        final_success: Optional[bool] = None,
        agent_claimed_success: Optional[bool] = None,
        trace_complete: bool = True,
    ) -> Dict[str, Any]:
        steps = self.recorder.load_steps()
        failure_labels = []
        blocked_actions = 0
        stuck_loop = False
        false_success = False
        for step in steps:
            decision = step.get("runtime_decision") if isinstance(step.get("runtime_decision"), dict) else {}
            verification = step.get("progress_verification") if isinstance(step.get("progress_verification"), dict) else {}
            diagnosis = step.get("diagnosis") if isinstance(step.get("diagnosis"), dict) else {}
            label = diagnosis.get("label") or verification.get("diagnosis_label") or decision.get("diagnosis_label")
            if label:
                failure_labels.append(str(label))
            if decision and not decision.get("allowed", False):
                blocked_actions += 1
            if label == "stuck_loop":
                stuck_loop = True
            if verification.get("false_success_candidate"):
                false_success = True
        summary = EpisodeSummary(
            episode_id=self.episode_id,
            goal=goal or self._last_goal,
            agent_name=self.config.agent_name,
            runtime_mode=self.config.runtime_mode,
            final_success=final_success,
            agent_claimed_success=agent_claimed_success,
            false_success=false_success if false_success else None,
            failure_labels=sorted(set(failure_labels)),
            steps_total=len(steps),
            blocked_actions=blocked_actions,
            stuck_loop_detected=stuck_loop,
            trace_complete=trace_complete,
        )
        return self.recorder.write_summary(summary)

    def export_trace(self) -> Dict[str, str]:
        return self.recorder.export_trace()

    def _agent_meta(self) -> Dict[str, Any]:
        payload = dict(self.config.agent_meta or {})
        payload.setdefault("agent_name", self.config.agent_name)
        payload.setdefault("runtime_mode", self.config.runtime_mode)
        return payload
