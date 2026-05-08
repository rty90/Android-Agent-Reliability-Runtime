"""Small deterministic procedures that can safely outrank model proposals.

Procedures are intentionally narrow and evidence-driven. They are useful when
the UI state already exposes a clear safe move, but they should remain
auditable hints rather than a hidden app-specific automation layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from app.task_types import TASK_GUIDED_UI_TASK, extract_message_body
from app.ui_facts import candidate_text, find_primary_input, find_search_surface, lower
from app.ui_state import _extract_search_query, _goal_looks_search


@dataclass(frozen=True)
class ProcedureDecision(object):
    name: str
    skill: Optional[str]
    args: Dict[str, Any]
    confidence: float
    reason_summary: str


ProcedureMatcher = Callable[
    [str, str, Mapping[str, Any], Mapping[str, Any], Sequence[Mapping[str, Any]]],
    Optional[ProcedureDecision],
]


@dataclass(frozen=True)
class Procedure(object):
    name: str
    description: str
    matcher: ProcedureMatcher


def resolve_guided_ui_procedure(
    goal: str,
    task_type: str,
    screen_summary: Mapping[str, Any],
    ui_state: Mapping[str, Any],
    recent_actions: Optional[Sequence[Mapping[str, Any]]] = None,
    allowed_names: Optional[Sequence[str]] = None,
) -> Optional[ProcedureDecision]:
    if task_type != TASK_GUIDED_UI_TASK:
        return None
    allowed = set(allowed_names or [])
    for procedure in build_procedure_registry():
        if allowed and procedure.name not in allowed:
            continue
        decision = procedure.matcher(goal, task_type, screen_summary, ui_state, recent_actions or [])
        if decision:
            return decision
    return None


def build_procedure_registry() -> List[Procedure]:
    """Order matters: blockers first, then task-directed input procedures."""

    return [
        Procedure(
            name="clear_primary_blocker",
            description="Use the blocker policy action for permission dialogs, modals, errors, and loading states.",
            matcher=_match_clear_primary_blocker,
        ),
        Procedure(
            name="browser_search_via_visible_search_surface",
            description="Use a safe browser search intent when a web search surface is visible but not necessarily an EditText.",
            matcher=_match_browser_search_surface,
        ),
        Procedure(
            name="enter_text_into_focused_input",
            description="When a usable input is available, enter quoted text or a synthesized search query.",
            matcher=_match_enter_text_into_input,
        ),
    ]


def _match_clear_primary_blocker(
    goal: str,
    task_type: str,
    screen_summary: Mapping[str, Any],
    ui_state: Mapping[str, Any],
    recent_actions: Sequence[Mapping[str, Any]],
) -> Optional[ProcedureDecision]:
    blocker = ui_state.get("primary_blocker")
    if not isinstance(blocker, Mapping):
        return None
    action = blocker.get("suggested_action")
    if not isinstance(action, Mapping):
        return None
    skill = str(action.get("skill") or "").strip()
    if skill not in {"tap", "back", "wait"}:
        return None
    args = action.get("args") if isinstance(action.get("args"), Mapping) else {}
    blocker_type = str(blocker.get("type") or "blocker").strip()
    return ProcedureDecision(
        name="clear_primary_blocker",
        skill=skill,
        args=dict(args),
        confidence=0.93,
        reason_summary="Procedure clear_primary_blocker selected: {0}.".format(blocker_type),
    )


def _match_browser_search_surface(
    goal: str,
    task_type: str,
    screen_summary: Mapping[str, Any],
    ui_state: Mapping[str, Any],
    recent_actions: Sequence[Mapping[str, Any]],
) -> Optional[ProcedureDecision]:
    if ui_state.get("primary_blocker") or not _goal_looks_search(goal):
        return None
    text = _extract_search_query(goal)
    if not text:
        return None
    surface = _find_search_target(screen_summary, ui_state)
    if not surface or not _looks_like_browser_search_surface(screen_summary, surface):
        return None
    return ProcedureDecision(
        name="browser_search_via_intent",
        skill="search_in_app",
        args={"query": text, "prefer_intent": True, "press_enter": True},
        confidence=0.92,
        reason_summary="Procedure browser_search_via_intent selected for a visible browser search surface.",
    )


def _match_enter_text_into_input(
    goal: str,
    task_type: str,
    screen_summary: Mapping[str, Any],
    ui_state: Mapping[str, Any],
    recent_actions: Sequence[Mapping[str, Any]],
) -> Optional[ProcedureDecision]:
    if ui_state.get("primary_blocker"):
        return None

    input_target = _find_input_target(screen_summary, ui_state)
    if not input_target:
        return None

    requested_text = extract_message_body(goal)
    press_enter = False
    dismiss_overlays_first = False
    if requested_text:
        text = requested_text
    elif _goal_looks_search(goal):
        if not _looks_like_search_input(input_target):
            return None
        text = _extract_search_query(goal)
        if not text:
            return None
        if _looks_like_browser_search_surface(screen_summary, input_target):
            return ProcedureDecision(
                name="browser_search_via_intent",
                skill="search_in_app",
                args={"query": text, "prefer_intent": True, "press_enter": True},
                confidence=0.92,
                reason_summary="Procedure browser_search_via_intent selected for a focused browser search surface.",
            )
        press_enter = True
        dismiss_overlays_first = True
    else:
        return None

    if _goal_looks_search(goal) and any(
        str(action.get("action") or "").strip() == "tap" and bool(action.get("success"))
        for action in recent_actions
    ):
        dismiss_overlays_first = True

    args: Dict[str, Any] = {
        "text": text,
        "target": input_target.get("label") or input_target.get("hint") or "Input",
    }
    target_id = str(input_target.get("target_id") or "").strip()
    if target_id:
        args["target_id"] = target_id
        args["action_id"] = "type:{0}".format(target_id)
    if press_enter:
        args["press_enter"] = True
    if dismiss_overlays_first:
        args["dismiss_overlays_first"] = True

    return ProcedureDecision(
        name="enter_text_into_focused_input",
        skill="type_text",
        args=args,
        confidence=0.91,
        reason_summary="Procedure enter_text_into_focused_input selected for the available input.",
    )


def _find_input_target(
    screen_summary: Mapping[str, Any],
    ui_state: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    primary = ui_state.get("primary_input")
    primary_target_id = ""
    if isinstance(primary, Mapping):
        primary_target_id = str(primary.get("target_id") or "").strip()
    if primary_target_id:
        for candidate in screen_summary.get("possible_targets", []):
            if isinstance(candidate, dict) and str(candidate.get("target_id") or "").strip() == primary_target_id:
                return candidate
    return find_primary_input(dict(screen_summary))


def _find_search_target(
    screen_summary: Mapping[str, Any],
    ui_state: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    primary = ui_state.get("primary_search_surface")
    primary_target_id = ""
    if isinstance(primary, Mapping):
        primary_target_id = str(primary.get("target_id") or "").strip()
    if primary_target_id:
        for candidate in screen_summary.get("possible_targets", []):
            if isinstance(candidate, dict) and str(candidate.get("target_id") or "").strip() == primary_target_id:
                return candidate
    return find_search_surface(dict(screen_summary))


def _looks_like_search_input(input_target: Mapping[str, Any]) -> bool:
    combined = candidate_text(dict(input_target))
    return any(marker in combined for marker in ("search", "url", "query", "address", "find"))


def _looks_like_browser_search_surface(
    screen_summary: Mapping[str, Any],
    input_target: Mapping[str, Any],
) -> bool:
    combined = candidate_text(dict(input_target))
    if "url_bar" in combined or "location_bar" in combined:
        return True
    app_name = lower(screen_summary.get("app"))
    focus = lower(screen_summary.get("focus"))
    return any(marker in combined for marker in ("search", "url", "address")) and any(
        marker in "{0} {1}".format(app_name, focus)
        for marker in ("chrome", "browser")
    )
