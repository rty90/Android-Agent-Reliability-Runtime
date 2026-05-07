from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        _lower(candidate.get(key))
        for key in ("label", "resource_id", "content_desc", "class_name", "hint")
    )


def _has_input_target(possible_targets: Iterable[Dict[str, Any]]) -> bool:
    for candidate in possible_targets:
        if not isinstance(candidate, dict):
            continue
        class_name = _lower(candidate.get("class_name"))
        is_text_input = "edittext" in class_name or "autocompletetextview" in class_name
        is_search_surface = "searchview" in class_name
        if bool(candidate.get("focused")):
            return True
        combined = _candidate_text(candidate)
        if is_search_surface:
            return True
        if is_text_input and any(marker in combined for marker in ("search", "url", "query", "address", "write here")):
            return True
        if "url_bar" in combined or "location_bar" in combined:
            return True
    return False


def _line_matches(lines: Iterable[str], *markers: str) -> List[str]:
    matched = []
    wanted = tuple(marker.lower() for marker in markers)
    for line in lines:
        lowered = line.lower()
        if any(marker in lowered for marker in wanted):
            matched.append(line.strip())
    return matched


def _extract_ime_package(window_dump: str, input_method_dump: str) -> str:
    for line in (window_dump or "").splitlines():
        if "InputMethod" not in line:
            continue
        match = re.search(r"package=([A-Za-z0-9_.]+)", line)
        if match:
            return match.group(1)
    for pattern in (
        r"mCurImeId=([A-Za-z0-9_.]+)/",
        r"mSelectedImeId=([A-Za-z0-9_.]+)/",
        r"mCurId=([A-Za-z0-9_.]+)/",
        r"mInputMethodId=([A-Za-z0-9_.]+)/",
    ):
        match = re.search(pattern, input_method_dump or "")
        if match:
            return match.group(1)
    return ""


def detect_system_overlay(
    screen_summary: Dict[str, Any],
    window_dump: str = "",
    input_method_dump: str = "",
) -> Dict[str, Any]:
    """Detect system-owned overlays that are invisible to the app UI tree.

    uiautomator often reports only the target app hierarchy, while the final
    screenshot can include input-method or system windows composited above it.
    This function intentionally returns structured evidence instead of app-
    specific instructions so higher layers can choose a recovery action.
    """

    window_dump = _text(window_dump)
    input_method_dump = _text(input_method_dump)
    window_lower = window_dump.lower()
    input_lower = input_method_dump.lower()
    possible_targets = screen_summary.get("possible_targets", [])
    has_input_target = _has_input_target(possible_targets)

    evidence: List[str] = []
    window_lines = window_dump.splitlines()
    input_lines = input_method_dump.splitlines()

    ime_window_present = "inputmethod" in window_lower
    ime_showing = any(
        marker in window_lower or marker in input_lower
        for marker in ("mimeshowing=true", "mimewindowvis=3")
    )
    handwriting_enabled = "isstylushandwritingenabled=true" in input_lower
    direct_writing_enabled = "restrictdirectwritingarea=true" in input_lower
    active_handwriting_gestures = bool(
        re.search(r"supportedhandwritinggesturetypes=(?!\(?none\)?)([a-z_|]+)", input_lower)
    )
    handwriting_signals = handwriting_enabled or (direct_writing_enabled and active_handwriting_gestures)

    if ime_window_present:
        evidence.append("dumpsys window reports an InputMethod window")
    if "mimeshowing=true" in window_lower or "mimeshowing=true" in input_lower:
        evidence.append("IME is marked as showing")
    if handwriting_signals:
        evidence.append("input method has active handwriting/direct-writing signals")
    if has_input_target:
        evidence.append("current app UI has an active or likely text input target")

    focused_system_lines = [
        line.strip()
        for line in window_lines
        if any(marker in line.lower() for marker in ("mcurrentfocus", "mfocusedapp", "mfocusedwindow"))
        and any(marker in line.lower() for marker in ("notificationshade", "notification shade"))
    ]
    shade_expanded_signal = any(
        marker in window_lower
        for marker in (
            "mexpandedvisible=true",
            "mpanelexpanded=true",
            "mshadeexpanded=true",
            "notification shade expanded=true",
        )
    )
    notification_expanded = bool(focused_system_lines) or shade_expanded_signal

    if notification_expanded:
        return {
            "present": True,
            "scope": "system",
            "type": "notification_or_system_shade",
            "blocks_input": True,
            "confidence": 0.78,
            "recommended_recovery": "back",
            "evidence": focused_system_lines[:5] or ["system notification shade appears expanded"],
        }

    if ime_window_present and has_input_target:
        overlay_type = "handwriting_input_method" if handwriting_signals else "input_method"
        confidence = 0.82 if handwriting_signals else 0.62
        blocks_input = bool(ime_showing and handwriting_signals)
        if ime_showing and handwriting_signals:
            confidence = 0.88
        return {
            "present": True,
            "scope": "system",
            "type": overlay_type,
            "blocks_input": blocks_input,
            "confidence": confidence,
            "recommended_recovery": "back" if blocks_input else "none",
            "package": _extract_ime_package(window_dump, input_method_dump),
            "evidence": evidence[:8]
            + _line_matches(input_lines, "stylus", "handwriting", "restrictDirectWritingArea")[:5],
        }

    return {
        "present": False,
        "scope": "system",
        "type": "none",
        "blocks_input": False,
        "confidence": 0.0,
        "recommended_recovery": "none",
        "evidence": evidence[:5],
    }
