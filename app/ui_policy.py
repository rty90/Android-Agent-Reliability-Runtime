"""Policy detectors for UI states that should gate normal agent actions.

The detectors in this file intentionally produce generic blocker records:
type, severity, reason, evidence, and a suggested safe action. They should not
encode a full task plan; the orchestrator and verifier decide what to do next.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ui_facts import (
    action_from_candidate,
    find_clickable_target,
    lower,
    screen_corpus,
    text,
)


def _looks_like_stylus_overlay(corpus: str) -> bool:
    strong_markers = (
        "try out your stylus",
        "write here",
        "use your stylus",
        "handwriting is automatically converted to text",
        "stylus",
    )
    if not any(marker in corpus for marker in strong_markers):
        return False
    controls = ("cancel", "next", "reset", "write", "delete", "select", "insert")
    return sum(1 for marker in controls if marker in corpus) >= 2


def _permission_blocker(screen_summary: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    if not any(marker in corpus for marker in ("allow", "don\u2019t allow", "don't allow", "permission")):
        return None
    if not any(
        marker in corpus
        for marker in (
            "send you notifications",
            "access",
            "permission",
            "take pictures",
            "record video",
            "camera",
            "microphone",
            "location",
        )
    ):
        return None
    target = find_clickable_target(screen_summary, ("Allow", "While using the app", "Only this time"))
    if not target:
        return None
    return {
        "type": "permission_dialog",
        "severity": "blocking",
        "reason": "A permission dialog is blocking the target app.",
        "suggested_action": action_from_candidate("tap", target),
    }


def _onboarding_blocker(screen_summary: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    markers = (
        "take me to gmail",
        "got it",
        "not now",
        "skip",
        "welcome",
        "set up email",
        "google meet, now in gmail",
        "try another way",
    )
    if not any(marker in corpus for marker in markers):
        return None
    target = find_clickable_target(
        screen_summary,
        (
            "TAKE ME TO GMAIL",
            "Got it",
            "Close",
            "Get started",
            "Not now",
            "Skip",
            "Next",
            "Continue",
        ),
    )
    if not target:
        return None
    return {
        "type": "onboarding_dialog",
        "severity": "blocking",
        "reason": "An onboarding or setup surface must be dismissed before the task can continue.",
        "suggested_action": action_from_candidate("tap", target),
    }


def _blocking_dialog_blocker(screen_summary: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    markers = (
        "blocking dialog",
        "this dialog blocks",
        "blocks the current task",
        "try this new feature",
    )
    if not any(marker in corpus for marker in markers):
        return None
    target = find_clickable_target(
        screen_summary,
        (
            "Allow",
            "Continue",
            "Get started",
            "Not now",
            "Skip",
            "Cancel",
            "OK",
        ),
    )
    if not target:
        return None
    return {
        "type": "blocking_dialog",
        "severity": "blocking",
        "reason": "A modal dialog or feature prompt is blocking the current task.",
        "suggested_action": action_from_candidate("tap", target),
    }


def _error_blocker(screen_summary: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    if not any(marker in corpus for marker in ("something went wrong", "try again", "error", "failed")):
        return None
    target = find_clickable_target(screen_summary, ("Retry", "Try again", "Reload", "Refresh", "OK"))
    if not target:
        return None
    return {
        "type": "error_state",
        "severity": "blocking",
        "reason": "An error state is visible and offers a recovery action.",
        "suggested_action": action_from_candidate("tap", target),
    }


def _loading_blocker(screen_summary: Dict[str, Any], corpus: str) -> Optional[Dict[str, Any]]:
    complete_markers = ("loaded successfully", "complete", "done")
    visible_loading = False
    for item in screen_summary.get("visible_text", []):
        value = lower(item)
        if value in {"loading", "loading...", "please wait", "please wait..."}:
            visible_loading = True
            break
        if value.startswith("loading ") and "show loading" not in value:
            visible_loading = True
            break
    progress_control = any(
        isinstance(candidate, dict)
        and any(marker in lower(candidate.get("class_name")) for marker in ("progressbar", "progress bar"))
        for candidate in screen_summary.get("possible_targets", [])
    )
    if not visible_loading and not progress_control:
        return None
    if not progress_control and any(marker in corpus for marker in complete_markers):
        return None
    return {
        "type": "loading_state",
        "severity": "transient",
        "reason": "A loading state is visible; wait for it to settle before choosing the next action.",
        "suggested_action": {"skill": "wait", "args": {"seconds": 2}},
    }


def _system_overlay_blocker(screen_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    overlay = screen_summary.get("system_overlay")
    if not isinstance(overlay, dict):
        return None
    if not overlay.get("present") or not overlay.get("blocks_input"):
        return None
    overlay_type = text(overlay.get("type")) or "system_overlay"
    recovery = text(overlay.get("recommended_recovery")) or "back"
    skill = "back" if recovery in ("back", "none") else recovery
    evidence = overlay.get("evidence") if isinstance(overlay.get("evidence"), list) else []
    reason = "A system-level overlay is covering or intercepting the target app."
    if "input_method" in overlay_type:
        reason = "A system input-method overlay is covering or intercepting the focused input."
    return {
        "type": "system_{0}".format(overlay_type),
        "severity": "blocking",
        "reason": reason,
        "suggested_action": {"skill": skill, "args": {}},
        "source": "system_overlay",
        "confidence": overlay.get("confidence", 0.0),
        "evidence": evidence[:5],
    }


def detect_blockers(screen_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect blockers from strongest signal to weaker content heuristics."""

    corpus = screen_corpus(screen_summary)
    blockers: List[Dict[str, Any]] = []
    if _looks_like_stylus_overlay(corpus):
        blockers.append(
            {
                "type": "input_blocking_overlay",
                "severity": "blocking",
                "reason": "A stylus or handwriting overlay is covering the focused input.",
                "suggested_action": {"skill": "back", "args": {}},
            }
        )
    system_overlay = _system_overlay_blocker(screen_summary)
    if system_overlay:
        blockers.append(system_overlay)
    for detector in (
        _permission_blocker,
        _error_blocker,
        _loading_blocker,
        _blocking_dialog_blocker,
        _onboarding_blocker,
    ):
        blocker = detector(screen_summary, corpus)
        if blocker:
            blockers.append(blocker)
    return blockers
