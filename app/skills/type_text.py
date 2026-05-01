from __future__ import annotations

import time
from typing import Any, Dict, Mapping

from app.affordances import find_candidate_by_target_id
from app.skills.base import BaseSkill, SkillContext
from app.skills.read_screen import read_screen_summary


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _extract_candidate_text(candidate: Mapping[str, Any]) -> str:
    label = str(candidate.get("label") or "").strip()
    hint = str(candidate.get("hint") or "").strip()
    return label or hint


def _text_input_applied(
    before_summary: Mapping[str, Any],
    after_summary: Mapping[str, Any],
    expected_text: str,
    target_id: str | None,
) -> bool:
    expected_norm = _normalize_text(expected_text)
    if not expected_norm:
        return False

    after_targets = after_summary.get("possible_targets") or []
    for candidate in after_targets:
        candidate_text = _normalize_text(_extract_candidate_text(candidate))
        if expected_norm and expected_norm in candidate_text:
            return True

    after_visible = _normalize_text(" ".join(after_summary.get("visible_text") or []))
    if expected_norm and expected_norm in after_visible:
        return True

    if not target_id:
        return False

    before_candidate = find_candidate_by_target_id(before_summary, target_id)
    after_candidate = find_candidate_by_target_id(after_summary, target_id)
    if not after_candidate:
        return False
    before_text = _normalize_text(_extract_candidate_text(before_candidate or {}))
    after_text = _normalize_text(_extract_candidate_text(after_candidate))
    return bool(after_text and after_text != before_text)


def _is_blocking_input_method_overlay(summary: Mapping[str, Any]) -> bool:
    overlay = summary.get("system_overlay") or {}
    overlay_type = str(overlay.get("type") or "").lower()
    return bool(
        overlay.get("blocks_input")
        and overlay.get("recommended_recovery") == "back"
        and "input_method" in overlay_type
    )


class TypeTextSkill(BaseSkill):
    name = "type_text"

    def execute(self, args: Mapping[str, Any], context: SkillContext) -> Dict[str, Any]:
        text = args.get("text")
        if text is None:
            return self.result(success=False, detail="type_text requires a text argument.")
        target_id = args.get("target_id")
        if not target_id and isinstance(args.get("action_id"), str) and str(args.get("action_id")).startswith("type:"):
            target_id = str(args.get("action_id")).split(":", 1)[1]
        summary = context.state.screen_summary or read_screen_summary(
            context.adb,
            "data/tmp/type_text_lookup.xml",
            runtime_config=context.runtime_config,
        )
        context.state.update_screen_summary(summary)
        candidate = None
        if target_id:
            candidate = find_candidate_by_target_id(summary, str(target_id))
            if not candidate or not candidate.get("bounds"):
                return self.result(
                    success=False,
                    detail="Unable to find text input target_id: {0}".format(target_id),
                )
        dismiss_overlays_first = bool(args.get("dismiss_overlays_first"))
        candidate_focused = bool((candidate or {}).get("focused"))
        if dismiss_overlays_first:
            context.adb.back()
        if candidate and not (dismiss_overlays_first and candidate_focused):
            bounds = candidate["bounds"]
            context.adb.tap(bounds["center_x"], bounds["center_y"])
        input_backend = context.adb.input_text_best_effort(str(text))
        if args.get("press_enter"):
            context.adb.keyevent(66)
        after_summary = read_screen_summary(
            context.adb,
            "data/tmp/type_text_verify.xml",
            runtime_config=context.runtime_config,
        )
        context.state.update_screen_summary(after_summary)
        if _text_input_applied(summary, after_summary, str(text), str(target_id) if target_id else None):
            return self.result(
                success=True,
                detail="Text input completed.",
                data={"input_backend": input_backend},
            )

        retry_backend = None
        retry_summary = None
        if _is_blocking_input_method_overlay(after_summary):
            context.adb.back()
            time.sleep(0.3)
            if candidate and not candidate_focused:
                bounds = candidate["bounds"]
                context.adb.tap(bounds["center_x"], bounds["center_y"])
                time.sleep(0.2)
            retry_backend = context.adb.input_text_best_effort(str(text))
            if args.get("press_enter"):
                context.adb.keyevent(66)
            retry_summary = read_screen_summary(
                context.adb,
                "data/tmp/type_text_verify_retry.xml",
                runtime_config=context.runtime_config,
            )
            context.state.update_screen_summary(retry_summary)
            if _text_input_applied(summary, retry_summary, str(text), str(target_id) if target_id else None):
                return self.result(
                    success=True,
                    detail="Text input completed after dismissing a blocking input-method overlay.",
                    data={
                        "input_backend": retry_backend,
                        "previous_input_backend": input_backend,
                        "overlay_recovery": "back_then_retry",
                    },
                )

        failure_data = {
            "input_backend": input_backend,
            "before_summary": summary,
            "after_summary": after_summary,
        }
        if retry_backend is not None:
            failure_data["retry_input_backend"] = retry_backend
        if retry_summary is not None:
            failure_data["retry_after_summary"] = retry_summary
        return self.result(
            success=False,
            detail=(
                "Text input did not change the UI after using {0}. "
                "The focused field still looks unchanged."
            ).format(input_backend),
            data=failure_data,
        )
