from __future__ import annotations

import re
from urllib.parse import quote
from typing import Any, Dict, Mapping

from app.skills.base import BaseSkill, SkillContext
from app.skills.read_screen import read_screen_summary
from app.skills.tap import _find_target
from app.skills.targeting import find_fallback_target
from app.ui_facts import find_search_surface


SEARCH_TARGETS = ("search", "find")
SITE_SEARCH_TEMPLATES = {
    "bilibili": "https://search.bilibili.com/all?keyword={query}",
    "youtube": "https://www.youtube.com/results?search_query={query}",
    "github": "https://github.com/search?q={query}",
    "wikipedia": "https://en.wikipedia.org/wiki/Special:Search?search={query}",
    "reddit": "https://www.reddit.com/search/?q={query}",
}


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key) or "").strip().lower()
        for key in ("label", "resource_id", "content_desc", "class_name", "hint")
    )


def _best_input_target(summary: Mapping[str, Any]) -> Dict[str, Any]:
    best_target: Dict[str, Any] = {}
    best_score = -1
    for candidate in summary.get("possible_targets", []):
        if not isinstance(candidate, dict):
            continue
        class_name = str(candidate.get("class_name") or "").lower()
        if "edittext" not in class_name:
            continue
        score = 0
        if bool(candidate.get("focused")):
            score += 4
        if bool(candidate.get("clickable")):
            score += 1
        combined = _candidate_text(candidate)
        if any(marker in combined for marker in ("search", "url", "query", "address", "find", "location_bar")):
            score += 2
        if score > best_score:
            best_score = score
            best_target = candidate
    if not best_target:
        best_target = find_search_surface(dict(summary)) or {}
    return best_target


def _looks_like_browser_surface(summary: Mapping[str, Any]) -> bool:
    target = _best_input_target(summary)
    if not target:
        return False
    combined = _candidate_text(target)
    focus = str(summary.get("focus") or "").strip().lower()
    app_name = str(summary.get("app") or "").strip().lower()
    if "url_bar" in combined or "location_bar" in combined:
        return True
    if any(marker in combined for marker in ("search", "url", "address")) and any(
        marker in "{0} {1}".format(app_name, focus)
        for marker in ("chrome", "browser")
    ):
        return True
    return False


def _build_search_url(query: str) -> str:
    query_text = str(query or "").strip()
    lowered = query_text.lower()
    if re.match(r"^https?://", query_text, re.IGNORECASE):
        return query_text
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/.*)?$", query_text, re.IGNORECASE):
        return "https://{0}".format(query_text)
    for site_name, template in SITE_SEARCH_TEMPLATES.items():
        if lowered == site_name or lowered.startswith(site_name + " "):
            remainder = query_text[len(site_name) :].strip()
            if remainder:
                return template.format(query=quote(remainder))
    return "https://www.google.com/search?q={0}".format(quote(query_text))


class SearchInAppSkill(BaseSkill):
    name = "search_in_app"

    def execute(self, args: Mapping[str, Any], context: SkillContext) -> Dict[str, Any]:
        query = args.get("query")
        if not query:
            return self.result(success=False, detail="search_in_app requires a query.")

        summary = context.state.screen_summary or read_screen_summary(
            context.adb,
            "data/tmp/search_lookup.xml",
            runtime_config=context.runtime_config,
        )
        context.state.update_screen_summary(summary)

        if bool(args.get("prefer_intent")) or _looks_like_browser_surface(summary):
            app_name = str(summary.get("app") or "").strip()
            package_name = app_name if app_name and app_name != "unknown" else None
            url = _build_search_url(str(query))
            context.adb.open_url(url, package_name=package_name)
            return self.result(
                success=True,
                detail="Search opened through a browser intent.",
                data={
                    "fallback_used": False,
                    "target_name": "browser_intent",
                    "url": url,
                },
            )

        search_target = None
        for candidate in SEARCH_TARGETS:
            search_target = _find_target(summary, candidate)
            if search_target:
                break

        fallback_used = False
        if (not search_target or not search_target.get("bounds")) and args.get("target_key", "search"):
            search_target = find_fallback_target(
                context.runtime_config,
                context.state.current_page or summary.get("page", ""),
                str(args.get("target_key", "search")),
                context.adb.get_screen_size(),
            )
            fallback_used = bool(search_target)

        if not search_target or not search_target.get("bounds"):
            return self.result(
                success=False,
                detail="Unable to find search entry point.",
                data={"fallback_used": False, "target_name": "search"},
            )

        bounds = search_target["bounds"]
        context.adb.tap(bounds["center_x"], bounds["center_y"])
        context.adb.input_text(str(query))
        if args.get("press_enter", True):
            context.adb.keyevent(66)

        return self.result(
            success=True,
            detail="Search query entered.",
            data={"fallback_used": fallback_used, "target": search_target},
        )
