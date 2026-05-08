"""Progress guards for separating executed actions from useful progress.

The executor can know that a tap or text input command ran, but this module
tracks whether the action changed the task stage or visible state. That
distinction is the main defense against false success and action loops.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Sequence

from app.task_types import TASK_GUIDED_UI_TASK
from app.ui_state import normalize_ui_state


GUARDED_ACTIONS = {"tap", "type_text", "search_in_app", "back"}


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compact_action_args(args: Dict[str, Any]) -> Dict[str, Any]:
    compact: Dict[str, Any] = {}
    for key in ("target_id", "action_id", "target", "target_key", "prefer_intent", "press_enter"):
        if key in args and args.get(key) not in (None, ""):
            compact[key] = args.get(key)
    for sensitive_key in ("text", "query"):
        if args.get(sensitive_key):
            compact["{0}_hash".format(sensitive_key)] = _stable_hash(_text(args.get(sensitive_key)).lower())
            compact["{0}_length".format(sensitive_key)] = len(_text(args.get(sensitive_key)))
    return compact


def action_fingerprint(skill: str, args: Optional[Dict[str, Any]]) -> str:
    return _stable_hash({"skill": _text(skill), "args": _compact_action_args(dict(args or {}))})


def screen_fingerprint(screen_summary: Dict[str, Any]) -> str:
    visible = [_text(item).lower() for item in (screen_summary or {}).get("visible_text", [])[:12]]
    return _stable_hash(
        {
            "app": _text((screen_summary or {}).get("app")).lower(),
            "page": _text((screen_summary or {}).get("page")).lower(),
            "domain": _text((screen_summary or {}).get("current_domain")).lower(),
            "url": _text((screen_summary or {}).get("current_url")).lower(),
            "visible": visible,
        }
    )


def build_action_guard(
    goal: str,
    task_type: str,
    skill: str,
    args: Optional[Dict[str, Any]],
    screen_summary: Dict[str, Any],
    recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Snapshot the action and current task stage before execution."""

    ui_state = normalize_ui_state(
        goal=goal,
        task_type=task_type,
        screen_summary=screen_summary or {},
        recent_actions=recent_actions,
    )
    progress = ui_state.get("goal_progress") if isinstance(ui_state, dict) else {}
    return {
        "action_fingerprint": action_fingerprint(skill, args),
        "screen_fingerprint": screen_fingerprint(screen_summary or {}),
        "progress_stage": progress.get("stage") if isinstance(progress, dict) else "",
        "progress_status": progress.get("status") if isinstance(progress, dict) else "",
        "progress_done": bool(progress.get("done")) if isinstance(progress, dict) else False,
    }


def detect_repeated_no_progress(
    goal: str,
    task_type: str,
    recent_actions: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """Return a human-readable loop warning when recent guarded actions stall."""

    if task_type != TASK_GUIDED_UI_TASK:
        return None
    guarded_events = []
    for event in recent_actions[-5:]:
        if not event.get("success"):
            continue
        action = _text(event.get("action"))
        if action not in GUARDED_ACTIONS:
            continue
        guard = ((event.get("data") or {}).get("action_guard") or {})
        if not isinstance(guard, dict) or not guard.get("action_fingerprint"):
            continue
        guarded_events.append((event, guard))

    if len(guarded_events) < 2:
        return None

    latest_event, latest_guard = guarded_events[-1]
    if bool(latest_guard.get("progress_done")):
        return None
    repeated = [
        (event, guard)
        for event, guard in guarded_events
        if guard.get("action_fingerprint") == latest_guard.get("action_fingerprint")
    ]
    if len(repeated) < 2:
        return None

    latest_stage = _text(latest_guard.get("progress_stage"))
    stable_stage = latest_stage and all(_text(guard.get("progress_stage")) == latest_stage for _, guard in repeated[-2:])
    stable_screen = (
        len(repeated) >= 2
        and repeated[-1][1].get("screen_fingerprint") == repeated[-2][1].get("screen_fingerprint")
    )
    latest_action = _text(latest_event.get("action"))
    if latest_action == "type_text" and stable_stage:
        return (
            "Repeated the same text input while task progress stayed at '{0}'. "
            "The next step should change strategy instead of appending more text."
        ).format(latest_stage)
    if stable_stage and stable_screen:
        return (
            "Repeated action '{0}' did not change the UI or task progress "
            "(stage: {1})."
        ).format(latest_action, latest_stage)
    return None
