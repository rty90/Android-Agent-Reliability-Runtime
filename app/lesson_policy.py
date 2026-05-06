from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


FAILURE_LABELS = {
    "loading_loop",
    "permission_blocker",
    "modal_blocker",
    "wrong_page",
    "target_missing",
    "no_ui_change",
    "false_success",
    "uncertain_state",
    "unsafe_action_blocked",
}

COMPLETION_LABELS = {
    "coach_goal_done",
    "complete",
    "success",
}

PARTIAL_PROGRESS_LABELS = {
    "reach_search_results",
    "navigate_to_correct_page",
    "dismiss_overlay",
    "clear_blocker",
    "manual_continue",
}

RISKY_MARKERS = {
    "captcha",
    "verification",
    "verify you are human",
    "login",
    "log in",
    "sign in",
    "password",
    "payment",
    "pay",
    "purchase",
    "checkout",
    "transfer",
    "delete",
    "remove account",
}


@dataclass(frozen=True)
class LessonPolicyDecision(object):
    """Decision for whether a trace can become a reusable lesson."""

    storage_stage: str
    should_store_raw_trace: bool
    should_create_candidate: bool
    should_promote: bool
    should_auto_execute: bool
    confidence_cap: float
    reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalized_label(label: str) -> str:
    return str(label or "").strip().lower()


def _text_blob(*values: Any) -> str:
    parts: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, Mapping):
            parts.extend(str(item) for item in value.values())
            continue
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value)
            continue
        parts.append(str(value))
    return " ".join(parts).lower()


def _procedure_steps(procedure: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    steps = (procedure or {}).get("steps")
    if not isinstance(steps, list):
        return []
    return [dict(step) for step in steps if isinstance(step, Mapping)]


def has_risky_content(
    *,
    goal: str = "",
    resolution_label: str = "",
    trigger_reason: str = "",
    procedure: Optional[Mapping[str, Any]] = None,
    screen_text: Optional[Iterable[str]] = None,
) -> bool:
    steps = _procedure_steps(procedure)
    step_blob = _text_blob(*steps)
    text = _text_blob(goal, resolution_label, trigger_reason, step_blob, list(screen_text or []))
    return any(marker in text for marker in RISKY_MARKERS)


def evaluate_lesson_evidence(
    *,
    goal: str,
    task_type: str = "",
    app: str = "",
    resolution_label: str = "",
    trigger_reason: str = "",
    procedure: Optional[Mapping[str, Any]] = None,
    verified: bool = False,
    human_approved: bool = False,
    evidence_count: int = 1,
    before_visible_text: Optional[Sequence[str]] = None,
    after_visible_text: Optional[Sequence[str]] = None,
) -> LessonPolicyDecision:
    """Classify raw UI learning evidence into raw/candidate/promoted stages.

    The policy is intentionally conservative: a single successful-looking human
    demo can become a candidate hint, but not an auto-executed operational rule.
    """

    reasons: List[str] = []
    goal_text = str(goal or "").strip()
    label = _normalized_label(resolution_label)
    steps = _procedure_steps(procedure)
    risky = has_risky_content(
        goal=goal_text,
        resolution_label=label,
        trigger_reason=trigger_reason,
        procedure=procedure,
        screen_text=list(before_visible_text or []) + list(after_visible_text or []),
    )

    if not goal_text:
        return LessonPolicyDecision(
            storage_stage="rejected",
            should_store_raw_trace=False,
            should_create_candidate=False,
            should_promote=False,
            should_auto_execute=False,
            confidence_cap=0.0,
            reasons=["missing_goal"],
        )

    should_store_raw = True
    if label in FAILURE_LABELS:
        reasons.append("failure_label")
    elif label in COMPLETION_LABELS:
        reasons.append("completion_label")
    elif label in PARTIAL_PROGRESS_LABELS:
        reasons.append("partial_progress_label")
    else:
        reasons.append("unknown_resolution_label")

    has_reusable_shape = bool(steps) or bool(trigger_reason) or bool(before_visible_text) or bool(after_visible_text)
    should_create_candidate = has_reusable_shape and not (label in FAILURE_LABELS and not human_approved)
    if not should_create_candidate:
        reasons.append("candidate_not_reusable")

    if risky:
        reasons.append("risky_content")

    enough_evidence = int(evidence_count or 0) >= 2
    can_promote = (
        should_create_candidate
        and bool(verified)
        and bool(human_approved or enough_evidence)
        and label in COMPLETION_LABELS
        and not risky
    )

    if can_promote:
        stage = "promoted_lesson"
        confidence_cap = 0.95
        reasons.append("promotion_conditions_met")
    elif should_create_candidate:
        stage = "candidate_lesson"
        confidence_cap = 0.80
        if not verified:
            reasons.append("not_verified")
        if not human_approved and not enough_evidence:
            reasons.append("needs_human_approval_or_repeated_evidence")
    else:
        stage = "raw_trace"
        confidence_cap = 0.60

    return LessonPolicyDecision(
        storage_stage=stage,
        should_store_raw_trace=should_store_raw,
        should_create_candidate=should_create_candidate,
        should_promote=can_promote,
        should_auto_execute=False,
        confidence_cap=confidence_cap,
        reasons=sorted(set(reasons)),
    )


def apply_lesson_safety(
    procedure: Mapping[str, Any],
    *,
    decision: Optional[LessonPolicyDecision] = None,
    verified: bool = False,
) -> Dict[str, Any]:
    """Return a copy of a learned procedure with conservative safety metadata."""

    safe_procedure = copy.deepcopy(dict(procedure or {}))
    safety = dict(safe_procedure.get("safety") or {})
    safety["auto_execute"] = False
    safety["needs_current_ui_validation"] = True
    safety["raw_memory_filtered"] = True
    safety["verified"] = bool(verified)
    if decision is not None:
        safety["lesson_stage"] = decision.storage_stage
        safety["promotion_required"] = not decision.should_promote
        safety["policy_reasons"] = list(decision.reasons)
    else:
        safety.setdefault("lesson_stage", "promoted_lesson" if verified else "candidate_lesson")
        safety.setdefault("promotion_required", not bool(verified))
    safe_procedure["safety"] = safety
    return safe_procedure
