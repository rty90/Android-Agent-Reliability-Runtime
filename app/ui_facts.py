from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


def text(value: Any) -> str:
    return str(value or "").strip()


def lower(value: Any) -> str:
    return text(value).lower()


def candidate_text(candidate: Dict[str, Any]) -> str:
    return " ".join(
        lower(candidate.get(key))
        for key in ("label", "resource_id", "content_desc", "class_name", "hint")
    )


def screen_corpus(screen_summary: Dict[str, Any]) -> str:
    fragments = [lower(item) for item in screen_summary.get("visible_text", [])]
    for candidate in screen_summary.get("possible_targets", []):
        if isinstance(candidate, dict):
            fragments.append(candidate_text(candidate))
    return " ".join(fragment for fragment in fragments if fragment)


def find_clickable_target(
    screen_summary: Dict[str, Any],
    labels: Sequence[str],
) -> Optional[Dict[str, Any]]:
    wanted = [lower(label) for label in labels if text(label)]
    candidates = [
        candidate
        for candidate in screen_summary.get("possible_targets", [])
        if isinstance(candidate, dict) and bool(candidate.get("clickable"))
    ]
    for label in wanted:
        for candidate in candidates:
            combined = candidate_text(candidate)
            if label == combined or label in combined:
                return candidate
    return None


def find_primary_input(screen_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for candidate in screen_summary.get("possible_targets", []):
        if not isinstance(candidate, dict):
            continue
        class_name = lower(candidate.get("class_name"))
        if "edittext" not in class_name:
            continue
        score = 0
        if bool(candidate.get("focused")):
            score += 4
        if bool(candidate.get("clickable")):
            score += 1
        combined = candidate_text(candidate)
        if any(marker in combined for marker in ("search", "url", "query", "address", "find")):
            score += 2
        if score > best_score:
            best_score = score
            best = candidate
    return best


def action_from_candidate(skill: str, candidate: Dict[str, Any]) -> Dict[str, Any]:
    target = candidate.get("label") or candidate.get("content_desc") or candidate.get("resource_id") or ""
    args: Dict[str, Any] = {"target": target}
    target_id = text(candidate.get("target_id"))
    if target_id:
        args["target_id"] = target_id
        args["action_id"] = "{0}:{1}".format(skill, target_id)
    return {"skill": skill, "args": args}
