from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.memory import SQLiteMemory
from app.lesson_policy import apply_lesson_safety, evaluate_lesson_evidence


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except json.JSONDecodeError:
        return fallback


def _normalized_step_key(step: Dict[str, Any]) -> str:
    return json.dumps(
        {
            "skill": step.get("skill"),
            "target": step.get("target"),
            "text": step.get("text"),
            "query": step.get("query"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _safe_args(args: Dict[str, Any]) -> Dict[str, Any]:
    safe = {}
    for key in (
        "target",
        "target_app",
        "app_package",
        "text",
        "query",
        "prefer_intent",
        "press_enter",
        "dismiss_overlays_first",
    ):
        if key in args and args[key] not in ("", None):
            safe[key] = args[key]
    return safe


def _extract_steps(agent_actions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    seen_consecutive = ""
    for action in agent_actions:
        if not isinstance(action, dict):
            continue
        data = action.get("data") if isinstance(action.get("data"), dict) else {}
        suggestion = data.get("agent_suggestion") if isinstance(data.get("agent_suggestion"), dict) else {}
        skill = str(suggestion.get("skill") or "").strip()
        args = suggestion.get("args") if isinstance(suggestion.get("args"), dict) else {}
        if not skill or not args:
            continue
        safe_args = _safe_args(args)
        target = str(
            args.get("target")
            or args.get("target_app")
            or args.get("app_package")
            or args.get("query")
            or args.get("text")
            or ""
        ).strip()
        if skill == "open_app" and not target:
            continue
        step = {
            "skill": skill,
            "action": skill,
            "target": target,
            "args_template": safe_args,
            "reason_summary": str(suggestion.get("reason_summary") or "").strip()[:240],
        }
        screen = data.get("screen_summary") if isinstance(data.get("screen_summary"), dict) else {}
        if screen:
            step["observed_after_page"] = screen.get("page")
            step["observed_after_text"] = list(screen.get("visible_text", []))[:5]
        step_key = _normalized_step_key(
            {
                "skill": step.get("skill"),
                "target": step.get("target"),
                "text": step.get("args_template", {}).get("text"),
                "query": step.get("args_template", {}).get("query"),
            }
        )
        if step_key == seen_consecutive:
            continue
        seen_consecutive = step_key
        steps.append(step)
    return steps


def _build_procedure(row: sqlite3.Row) -> Dict[str, Any]:
    reflection = _load_json(row["reflection_json"], {})
    agent_actions = _load_json(row["agent_actions_json"], [])
    steps = _extract_steps(agent_actions)
    transition = reflection.get("human_observed_transition") if isinstance(reflection, dict) else {}
    before_tags = _load_json(row["before_tags_json"], [])
    after_tags = _load_json(row["after_tags_json"], [])
    stop_text = []
    if isinstance(transition, dict):
        stop_text = list(transition.get("after_visible_text") or [])[:8]
    return {
        "goal": row["intent"],
        "app": row["app"],
        "kind": "human_distilled_guided_ui_procedure",
        "steps": steps,
        "stop_conditions": {
            "resolution_label": row["resolution_label"],
            "after_page": transition.get("after_page") if isinstance(transition, dict) else "",
            "after_visible_text": stop_text,
            "after_tags": after_tags,
        },
        "starting_tags": before_tags,
        "safety": {
            "auto_execute": False,
            "needs_current_ui_validation": True,
            "raw_memory_filtered": True,
        },
    }


def _select_latest_per_goal(rows: Iterable[sqlite3.Row]) -> List[sqlite3.Row]:
    selected: "OrderedDict[str, sqlite3.Row]" = OrderedDict()
    for row in rows:
        key = "{0}\u241f{1}\u241f{2}".format(row["task_type"], row["app"] or "", row["intent"])
        if key not in selected:
            selected[key] = row
            continue
        current = selected[key]
        if row["resolution_label"] == "coach_goal_done" and current["resolution_label"] != "coach_goal_done":
            selected[key] = row
    return list(selected.values())


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Distill noisy guided-UI raw memories into filtered learned procedures.")
    parser.add_argument("--db", default="data/memory.db")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="Also distill partial manual reflections that did not end with coach_goal_done.",
    )
    parser.add_argument(
        "--include-unknown-app",
        action="store_true",
        help="Also distill reflections whose app key is unknown.",
    )
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Promote distilled candidates to verified operational hints. Use only after human review.",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    memory = SQLiteMemory(db_path=str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, task_type, app, page, intent, resolution_label, failed_skill,
                   failed_args_json, agent_actions_json, before_tags_json, after_tags_json,
                   reflection_json, timestamp, confidence
            FROM manual_reflections
            WHERE task_type = 'guided_ui_task'
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    finally:
        conn.close()

    distilled = 0
    skipped = 0
    for row in _select_latest_per_goal(rows):
        if row["resolution_label"] != "coach_goal_done" and not args.include_unverified:
            skipped += 1
            continue
        if str(row["app"] or "").strip().lower() in {"", "unknown"} and not args.include_unknown_app:
            skipped += 1
            continue
        procedure = _build_procedure(row)
        if len(procedure.get("steps", [])) < 2:
            skipped += 1
            continue
        transition = procedure.get("stop_conditions", {})
        decision = evaluate_lesson_evidence(
            goal=row["intent"],
            task_type=row["task_type"],
            app=row["app"] or "",
            resolution_label=row["resolution_label"],
            trigger_reason="manual_reflection",
            procedure=procedure,
            verified=row["resolution_label"] == "coach_goal_done",
            human_approved=bool(args.promote),
            evidence_count=1,
            before_visible_text=[],
            after_visible_text=transition.get("after_visible_text") if isinstance(transition, dict) else [],
        )
        procedure = apply_lesson_safety(
            procedure,
            decision=decision,
            verified=decision.should_promote,
        )
        ok = memory.upsert_learned_procedure(
            task_type=row["task_type"],
            app=row["app"] or "",
            intent=row["intent"],
            title="filtered coach procedure",
            procedure=procedure,
            source_refs=[
                {
                    "table": "manual_reflections",
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "resolution_label": row["resolution_label"],
                }
            ],
            confidence=min(float(row["confidence"] or 0.85), decision.confidence_cap),
            verified=decision.should_promote,
        )
        distilled += 1 if ok else 0

    print(json.dumps({"distilled": distilled, "skipped": skipped}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
