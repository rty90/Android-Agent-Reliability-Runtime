"""Translation adapter: legacy `app/` agent <-> A2R2 types.

This is the only place that knows the shape of the legacy agent's screen summary
(`app.skills.read_screen.read_screen_summary`) and its skill/args action format.
Keeping it in the adapter layer means the A2R2 runtime and policies stay generic
and agent-agnostic.

It contains no package-specific business logic; it only maps a generic Android
screen summary and a generic (skill, args) action into `Observation` /
`ProposedAction`.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from a2r2.policies.common import stable_hash
from a2r2.types import Observation, ProposedAction


# Skills that represent an actual UI side effect worth gating. read_screen /
# reason_about_page only refresh observations and are not gated.
ACTION_SKILLS = frozenset(
    {"tap", "type_text", "search_in_app", "swipe", "back", "open_app", "confirm_action"}
)

# How a legacy skill maps onto a generic A2R2 action_type.
_SKILL_TO_ACTION_TYPE = {
    "tap": "tap",
    "type_text": "type_text",
    "search_in_app": "type_text",
    "swipe": "swipe",
    "back": "back",
    "open_app": "open_app",
    "confirm_action": "confirm",
    "read_screen": "read_screen",
    "reason_about_page": "reason_about_page",
    "manual_intervention": "manual_handoff",
    "wait": "wait",
}


def observation_from_summary(
    summary: Optional[Mapping[str, Any]], screenshot_path: Optional[str] = None
) -> Observation:
    """Build an A2R2 Observation from a legacy read_screen summary."""
    summary = summary or {}
    xml_path = str(summary.get("ui_dump_path") or "") or None
    visible_text = list(summary.get("visible_text") or [])[:50]
    raw_targets = summary.get("possible_targets") or []
    compact_targets = _compact_targets(raw_targets)
    target_count = len(raw_targets) if isinstance(raw_targets, (list, tuple)) else 0
    fingerprint = stable_hash(
        {
            "package": summary.get("current_package") or summary.get("app"),
            "page": summary.get("page"),
            "visible_text": visible_text[:20],
            "current_url": summary.get("current_url"),
            "system_overlay": summary.get("system_overlay"),
        }
    )
    metadata = {
        "app": summary.get("app"),
        "page": summary.get("page"),
        "current_url": summary.get("current_url"),
        "current_domain": summary.get("current_domain"),
        "visible_text": visible_text,
        "system_overlay": summary.get("system_overlay"),
        "possible_target_count": target_count,
        "possible_target_total_count": summary.get("possible_target_total_count", target_count),
        "possible_targets_truncated": bool(
            summary.get("possible_targets_truncated")
            or (target_count > len(compact_targets))
            or (target_count >= 50 and "possible_targets_truncated" not in summary)
        ),
        "possible_targets": compact_targets,
    }
    # If the summary carries raw XML inline, forward it so readiness checks work
    # without re-reading the dump file. Real runs usually rely on xml_path (the
    # ui_dump_path file written by read_screen) instead.
    inline_xml = summary.get("xml_text") or summary.get("ui_xml")
    if inline_xml:
        metadata["xml_text"] = str(inline_xml)
    return Observation(
        screenshot_path=screenshot_path,
        xml_path=xml_path,
        activity=str(summary.get("focus") or "") or None,
        package=str(summary.get("current_package") or summary.get("app") or "") or None,
        ui_tree_hash=fingerprint,
        metadata=metadata,
    )


def _compact_targets(targets: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not isinstance(targets, (list, tuple)):
        return result
    for item in targets[:50]:
        if not isinstance(item, Mapping):
            continue
        compact: dict[str, Any] = {}
        for key in (
            "label",
            "text",
            "content_desc",
            "resource_id",
            "target_id",
            "class_name",
            "hint",
            "clickable",
            "enabled",
            "focusable",
        ):
            if key in item and item.get(key) is not None:
                compact[key] = item.get(key)
        if compact:
            result.append(compact)
    return result


def _target_text(args: Mapping[str, Any]) -> Optional[str]:
    for key in ("target", "expect_target", "text", "value", "query", "app", "app_name"):
        value = args.get(key)
        if value:
            return str(value)
    return None


def proposed_action_from_step(
    skill: str, args: Optional[Mapping[str, Any]] = None
) -> ProposedAction:
    """Build an A2R2 ProposedAction from a legacy (skill, args) pair.

    The full args are preserved in ``raw`` so the risk policy can scan typed
    text (e.g. typing a high-risk word) and so the trace is self-describing.
    """
    args = dict(args or {})
    action_type = _SKILL_TO_ACTION_TYPE.get(str(skill), str(skill))
    raw = {"agent_skill": str(skill), "agent_args": args}
    if str(skill) == "confirm_action":
        # The legacy agent only runs confirm_action when the user/flow confirmed.
        raw["confirmation_context"] = True
    return ProposedAction(
        action_type=action_type,
        target_text=_target_text(args),
        target_resource_id=str(args.get("resource_id") or "") or None,
        raw=raw,
    )


def is_action_skill(skill: str) -> bool:
    return str(skill) in ACTION_SKILLS


# Generic detail->label mapping for agent-reported failures. These are labels the
# *agent* effectively reported (via its failure detail), captured by shadow mode.
# They are deliberately kept separate from A2R2's own diagnosis labels: capturing
# a failure category is NOT the same as A2R2 having predicted it early.
def classify_agent_failure(detail: str, skill: Optional[str] = None) -> Optional[str]:
    text = str(detail or "").strip().lower()
    if not text:
        return None
    if "unable to find" in text or "tap target" in text or ("expect" in text and "not visible" in text):
        return "target_missing"
    if "expected page" in text or "wrong screen" in text:
        return "wrong_screen"
    if "did not change the ui" in text or "input did not change" in text or "still looks unchanged" in text:
        return "input_no_effect"
    if "manual intervention" in text or "input-blocking overlay" in text:
        return "manual_intervention_required"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    return "agent_failure"
