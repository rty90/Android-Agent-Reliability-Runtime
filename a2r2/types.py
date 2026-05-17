from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


TRACE_SCHEMA_VERSION = "trace.v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


@dataclass
class RuntimeConfig:
    traces_root: str = "data/traces"
    episode_id: Optional[str] = None
    agent_name: str = "external_agent"
    runtime_mode: str = "observe_gate_verify"
    readiness_repeat_threshold: int = 3
    progress_repeat_threshold: int = 3
    high_risk_requires_handoff: bool = True
    manual_handoff_on_uncertain_risk: bool = True
    default_wait_seconds: float = 2.0
    agent_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Observation:
    screenshot_path: Optional[str] = None
    xml_path: Optional[str] = None
    activity: Optional[str] = None
    package: Optional[str] = None
    ui_tree_hash: Optional[str] = None
    timestamp: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)


@dataclass
class ProposedAction:
    action_type: str
    x: Optional[int] = None
    y: Optional[int] = None
    target_text: Optional[str] = None
    target_resource_id: Optional[str] = None
    raw: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)


@dataclass
class RuntimeDecision:
    decision: str
    allowed: bool
    reason: str
    diagnosis_label: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    fallback_action: Optional[ProposedAction] = None
    confidence: float = 1.0
    policy_name: str = "runtime"

    def to_dict(self) -> Dict[str, Any]:
        payload = _jsonable(self)
        if self.fallback_action is not None:
            payload["fallback_action"] = self.fallback_action.to_dict()
        return payload


@dataclass
class ProgressVerification:
    progress_made: bool
    ui_changed: Optional[bool] = None
    xml_changed: Optional[bool] = None
    screenshot_changed: Optional[bool] = None
    agent_claimed_success: Optional[bool] = None
    false_success_candidate: bool = False
    diagnosis_label: Optional[str] = None
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)


@dataclass
class StepTrace:
    trace_id: str
    episode_id: str
    step_index: int
    goal: str
    agent_meta: Dict[str, Any]
    before_state: Dict[str, Any]
    proposed_action: Dict[str, Any]
    runtime_decision: Dict[str, Any]
    after_state: Dict[str, Any]
    progress_verification: Dict[str, Any]
    diagnosis: Dict[str, Any]
    timestamps: Dict[str, Any]
    artifacts_dir: str
    schema_version: str = TRACE_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)


@dataclass
class EpisodeSummary:
    episode_id: str
    goal: str
    agent_name: str
    runtime_mode: str
    final_success: Optional[bool] = None
    agent_claimed_success: Optional[bool] = None
    false_success: Optional[bool] = None
    failure_labels: List[str] = field(default_factory=list)
    steps_total: int = 0
    blocked_actions: int = 0
    stuck_loop_detected: bool = False
    trace_complete: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(self)
