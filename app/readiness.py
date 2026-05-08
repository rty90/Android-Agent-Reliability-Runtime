"""Readiness classification for deciding whether normal actions are allowed.

This layer is deliberately conservative. When the UI cannot be observed
reliably, it should return `uncertain` instead of letting a model hallucinate
progress from partial evidence.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.ui_facts import lower, screen_corpus


CHROME_PACKAGE = "com.android.chrome"

CHROME_RESOURCE_PREFIX = "com.android.chrome:id/"
CHROME_ONLY_LABELS = {
    "web view",
    "open the home page",
    "connection is secure",
    "new tab",
    "customize and control google chrome",
}
WEB_BLOCKER_MARKERS = (
    "captcha",
    "nocaptcha",
    "js_challenge",
    "challenge=",
    "verify you are human",
    "are you a human being",
    "i'm not a robot",
    "unusual traffic",
    "sign in to confirm",
    "before you continue",
    "accept all cookies",
    "cookie settings",
)


def classify_readiness(
    screen_summary: Dict[str, Any],
    blockers: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Classify whether the current UI is safe to act on.

    This layer is intentionally conservative. A browser URL can match the goal
    while the WebView is still blank, blocked, or invisible to uiautomator; in
    that case the runtime should not mark the task complete or keep acting.
    """

    blockers = list(blockers or [])
    hard_blockers = [
        blocker
        for blocker in blockers
        if isinstance(blocker, dict) and str(blocker.get("severity") or "") == "blocking"
    ]
    if hard_blockers:
        return _state(
            status="blocked",
            label=str(hard_blockers[0].get("type") or "blocker"),
            safe_actions=("wait", "diagnose", "manual_handoff"),
            reason=str(hard_blockers[0].get("reason") or "A blocking UI must be cleared first."),
            evidence=[str(hard_blockers[0].get("type") or "blocker")],
        )

    transient_blockers = [
        blocker
        for blocker in blockers
        if isinstance(blocker, dict) and str(blocker.get("severity") or "") == "transient"
    ]
    if transient_blockers:
        return _state(
            status="loading",
            label=str(transient_blockers[0].get("type") or "loading_state"),
            safe_actions=("wait", "diagnose", "manual_handoff"),
            reason=str(transient_blockers[0].get("reason") or "The UI is still loading."),
            evidence=[str(transient_blockers[0].get("type") or "loading_state")],
        )

    if _is_chrome_page(screen_summary):
        blocker = _browser_blocker(screen_summary)
        if blocker:
            return _state(
                status="blocked",
                label="web_blocker",
                safe_actions=("wait", "diagnose", "manual_handoff"),
                reason="The browser page appears blocked by a web challenge, consent, or login wall.",
                evidence=[blocker],
            )
        if not browser_web_content_visible(screen_summary):
            return _state(
                status="uncertain",
                label="browser_content_not_observable",
                safe_actions=("wait", "diagnose", "manual_handoff"),
                reason="Chrome has a URL, but uiautomator exposes only browser chrome or an empty WebView.",
                evidence=[str(screen_summary.get("current_url") or ""), "no non-Chrome web content in XML"],
            )

    return _state(
        status="ready",
        label="ready",
        safe_actions=("tap", "type_text", "search_in_app", "swipe", "wait", "diagnose", "manual_handoff"),
        reason="The UI appears ready for normal action selection.",
        evidence=[],
    )


def browser_web_content_visible(screen_summary: Dict[str, Any]) -> bool:
    if not _is_chrome_page(screen_summary):
        return True
    current_url = lower(screen_summary.get("current_url"))
    if not current_url:
        return True
    for item in screen_summary.get("visible_text", []):
        if _is_non_chrome_web_label(lower(item), current_url):
            return True
    for candidate in screen_summary.get("possible_targets", []):
        if not isinstance(candidate, dict):
            continue
        label = lower(candidate.get("label"))
        resource_id = lower(candidate.get("resource_id"))
        content_desc = lower(candidate.get("content_desc"))
        if resource_id.startswith(CHROME_RESOURCE_PREFIX):
            continue
        if label.startswith(CHROME_RESOURCE_PREFIX):
            continue
        if label in CHROME_ONLY_LABELS or content_desc in CHROME_ONLY_LABELS:
            continue
        if label.startswith("see ") and "tabs" in label:
            continue
        if label and label in current_url:
            continue
        if _is_non_chrome_web_label(label, current_url):
            return True
    return False


def _is_non_chrome_web_label(label: str, current_url: str) -> bool:
    if not label:
        return False
    if label.startswith(CHROME_RESOURCE_PREFIX):
        return False
    if label in CHROME_ONLY_LABELS:
        return False
    if label.startswith("see ") and "tabs" in label:
        return False
    if label in current_url:
        return False
    return label != "web view"


def _is_chrome_page(screen_summary: Dict[str, Any]) -> bool:
    app = lower(screen_summary.get("app"))
    current_package = lower(screen_summary.get("current_package"))
    current_url = lower(screen_summary.get("current_url"))
    return bool(current_url) and (app == CHROME_PACKAGE or current_package == CHROME_PACKAGE)


def _browser_blocker(screen_summary: Dict[str, Any]) -> str:
    corpus = " ".join(
        item
        for item in (
            lower(screen_summary.get("current_url")),
            lower(screen_summary.get("current_domain")),
            screen_corpus(screen_summary),
        )
        if item
    )
    for marker in WEB_BLOCKER_MARKERS:
        if marker in corpus:
            return marker
    return ""


def _state(
    status: str,
    label: str,
    safe_actions,
    reason: str,
    evidence: List[str],
) -> Dict[str, Any]:
    return {
        "status": status,
        "label": label,
        "safe_actions": list(safe_actions),
        "reason": reason,
        "evidence": [item for item in evidence if item],
    }
