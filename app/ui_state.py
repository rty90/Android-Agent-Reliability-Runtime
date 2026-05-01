from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from app.task_types import TASK_GUIDED_UI_TASK, extract_message_body
from app.ui_facts import find_primary_input, lower, screen_corpus, text
from app.ui_policy import detect_blockers

SITE_TERMS = ("bilibili", "youtube", "wikipedia", "amazon", "github", "reddit", "facebook")


def _goal_looks_search(goal: str) -> bool:
    normalized = lower(goal)
    return any(
        marker in normalized
        for marker in ("search ", "search for", "find ", "find videos", "find video", "look up", "look for")
    )


def _extract_search_query(goal: str) -> str:
    quoted = extract_message_body(goal)
    if quoted:
        return quoted
    normalized = lower(goal)
    patterns = (
        r"(?:find|look\s+for|look\s+up|search(?:\s+for)?)(?:\s+videos?)?(?:\s+about|\s+for)?\s+(.+)",
        r"(?:videos?\s+about)\s+(.+)",
    )
    query = ""
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            query = match.group(1).strip(" .,!?:;")
            break
    if not query:
        return ""
    query = re.sub(r"^(on|in|with)\s+", "", query).strip()
    query = re.sub(r"\s+(on|in)\s+(chrome|browser|web)\b.*$", "", query).strip()
    for site in SITE_TERMS:
        if site in normalized and site not in query:
            return "{0} {1}".format(site, query).strip()[:120]
    return query[:120]


def _query_tokens(goal: str) -> List[str]:
    query = _extract_search_query(goal)
    return [token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 2]


def _content_query_tokens(goal: str) -> List[str]:
    tokens = _query_tokens(goal)
    content_tokens = [token for token in tokens if token not in SITE_TERMS and token not in {"video", "videos"}]
    return content_tokens or tokens


def _requested_site_terms(goal: str) -> List[str]:
    normalized_goal = lower(goal)
    return [term for term in SITE_TERMS if term in normalized_goal]


def _search_goal_complete(goal: str, screen_summary: Dict[str, Any], corpus: str) -> bool:
    query_tokens = _query_tokens(goal)
    required_tokens = _content_query_tokens(goal)
    current_url = lower(screen_summary.get("current_url"))
    current_domain = lower(screen_summary.get("current_domain"))
    requested_sites = _requested_site_terms(goal)

    def has_requested_site_evidence() -> bool:
        if not requested_sites:
            return True
        return any(term in current_domain or term in current_url or term in corpus for term in requested_sites)

    if required_tokens and any(token in current_url for token in required_tokens) and (
        "/search" in current_url or "keyword=" in current_url or "q=" in current_url
    ):
        if any(term in current_domain for term in requested_sites):
            return True
        if not requested_sites:
            return True
    query_hits = sum(1 for token in required_tokens if token in corpus)
    if not required_tokens or query_hits < max(1, min(2, len(required_tokens))):
        return False
    if not has_requested_site_evidence():
        return False
    return "search?keyword=" in corpus or "search result" in corpus or "search results" in corpus


def assess_goal_progress(
    goal: str,
    task_type: str,
    screen_summary: Dict[str, Any],
    blockers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    corpus = screen_corpus(screen_summary)
    page = lower(screen_summary.get("page"))
    app = lower(screen_summary.get("app"))
    blockers = blockers or []

    if task_type != TASK_GUIDED_UI_TASK:
        return {"stage": "unknown", "status": "not_applicable", "done": False, "next_hint": ""}

    if blockers:
        return {
            "stage": "clear_blocker",
            "status": "blocked",
            "done": False,
            "next_hint": blockers[0].get("reason", "Clear the blocking UI first."),
        }

    normalized_goal = lower(goal)
    if "gmail" in normalized_goal and ("draft" in normalized_goal or "email" in normalized_goal):
        if all(marker in corpus for marker in ("send", "from")) and "compose" in corpus:
            return {
                "stage": "done",
                "status": "complete",
                "done": True,
                "next_hint": "Compose editor is visible; do not tap Send for a draft-only task.",
            }
        if "com.google.android.gm" not in app:
            return {"stage": "open_app", "status": "in_progress", "done": False, "next_hint": "Open Gmail."}
        if "compose" in corpus:
            return {"stage": "start_draft", "status": "ready", "done": False, "next_hint": "Tap Compose."}
        return {"stage": "navigate_home", "status": "in_progress", "done": False, "next_hint": "Reach Gmail inbox."}

    if _goal_looks_search(goal):
        if _search_goal_complete(goal, screen_summary, corpus):
            return {
                "stage": "done",
                "status": "complete",
                "done": True,
                "next_hint": "Search result state is visible.",
            }
        primary_input = find_primary_input(screen_summary)
        if primary_input:
            return {
                "stage": "enter_query",
                "status": "ready",
                "done": False,
                "next_hint": "Enter or submit search query: {0}".format(_extract_search_query(goal)),
            }
        if page.endswith("site") or page == "browser_site":
            return {
                "stage": "find_site_search",
                "status": "in_progress",
                "done": False,
                "next_hint": "Find the site search control.",
            }
        return {"stage": "open_search_surface", "status": "in_progress", "done": False, "next_hint": "Open search UI."}

    if "keep" in normalized_goal and ("note" in normalized_goal or "create" in normalized_goal):
        if page == "keep_editor":
            quoted = extract_message_body(goal)
            return {
                "stage": "fill_note" if quoted else "done",
                "status": "ready" if quoted else "complete",
                "done": not bool(quoted),
                "next_hint": "Type requested note text." if quoted else "Keep editor is open.",
            }
        return {"stage": "open_editor", "status": "in_progress", "done": False, "next_hint": "Open a new Keep note."}

    return {"stage": "unknown", "status": "unknown", "done": False, "next_hint": ""}


def normalize_ui_state(
    goal: str,
    task_type: str,
    screen_summary: Dict[str, Any],
    recent_actions: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    primary_input = find_primary_input(screen_summary)
    blockers = detect_blockers(screen_summary)
    input_context = None
    if primary_input and task_type == TASK_GUIDED_UI_TASK and (_goal_looks_search(goal) or bool(extract_message_body(goal))):
        input_overlay_types = ("input_blocking_overlay", "system_handwriting_input_method", "system_input_method")
        input_context_blockers = [
            blocker
            for blocker in blockers
            if isinstance(blocker, dict) and text(blocker.get("type")) in input_overlay_types
        ]
        if input_context_blockers:
            reason = "Input-method UI is present, but a focused input is available; continue entering the requested text."
            if _goal_looks_search(goal):
                reason = "Input-method UI is present, but a search input is available; continue entering the query."
            input_context = {
                "type": "input_method_overlay",
                "status": "active",
                "reason": reason,
                "suppressed_blockers": input_context_blockers,
            }
            blockers = [
                blocker
                for blocker in blockers
                if not (isinstance(blocker, dict) and text(blocker.get("type")) in input_overlay_types)
            ]
    progress = assess_goal_progress(goal, task_type, screen_summary, blockers=blockers)
    return {
        "app": screen_summary.get("app"),
        "page": screen_summary.get("page"),
        "current_url": screen_summary.get("current_url"),
        "current_domain": screen_summary.get("current_domain"),
        "blockers": blockers,
        "primary_blocker": blockers[0] if blockers else None,
        "primary_input": {
            "label": primary_input.get("label"),
            "target_id": primary_input.get("target_id"),
            "resource_id": primary_input.get("resource_id"),
            "focused": bool(primary_input.get("focused")),
        }
        if primary_input
        else None,
        "input_context": input_context,
        "goal_progress": progress,
        "recent_action_count": len(list(recent_actions or [])),
    }
