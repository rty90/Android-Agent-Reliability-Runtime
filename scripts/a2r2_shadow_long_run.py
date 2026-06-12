"""Run repeated real-agent A2R2 shadow episodes and summarize reliability.

This is a small soak-test wrapper around ``scripts/a2r2_shadow_agent_run.py``.
It keeps A2R2 in shadow mode: the legacy agent still executes normally, while
A2R2 records what it would have gated and how progress verification behaved.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.utils.adb import ADBClient, ADBError
from scripts.a2r2_shadow_agent_run import run_shadow


@dataclass(frozen=True)
class LongRunCase:
    name: str
    task: str
    task_type: str
    max_steps: int


DEFAULT_CASES = (
    LongRunCase("settings_inspect", "open settings and inspect current page", "guided_ui_task", 5),
    LongRunCase("chrome_bilibili", "open chrome and search for bilibili", "guided_ui_task", 6),
    LongRunCase("settings_tap_sound", "open settings and tap Sound and vibration", "guided_ui_task", 6),
    LongRunCase("reminder_a2r2", "create a reminder called A2R2 shadow test at 7pm", "create_reminder", 8),
    LongRunCase("chrome_emulator_fix", "open chrome and search for android emulator crash fix", "guided_ui_task", 6),
)


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def build_case_plan(cycles: int, max_cases: Optional[int] = None) -> List[LongRunCase]:
    plan: List[LongRunCase] = []
    for _ in range(max(1, int(cycles))):
        plan.extend(DEFAULT_CASES)
    if max_cases is not None and max_cases > 0:
        return plan[: int(max_cases)]
    return plan


def reset_device(adb: ADBClient) -> Dict[str, Any]:
    result: Dict[str, Any] = {"health_dump_ok": False, "health_detail": ""}
    try:
        adb.keyevent(224)  # KEYCODE_WAKEUP
        adb.home()
        time.sleep(0.9)
        dump = adb.run(
            "shell",
            "uiautomator",
            "dump",
            "/data/local/tmp/a2r2_shadow_long_health.xml",
            check=False,
            timeout=12,
        )
        combined = "{0}\n{1}".format(dump.stdout or "", dump.stderr or "").strip()
        result["health_dump_ok"] = dump.returncode == 0 and "ERROR:" not in combined
        result["health_detail"] = combined[:300]
        if not result["health_dump_ok"]:
            adb.keyevent(224)
            adb.home()
            time.sleep(1.0)
            retry = adb.run(
                "shell",
                "uiautomator",
                "dump",
                "/data/local/tmp/a2r2_shadow_long_health.xml",
                check=False,
                timeout=12,
            )
            retry_combined = "{0}\n{1}".format(retry.stdout or "", retry.stderr or "").strip()
            result["health_dump_ok"] = retry.returncode == 0 and "ERROR:" not in retry_combined
            result["health_detail"] = retry_combined[:300]
            result["health_retried"] = True
    except Exception as exc:
        result["health_detail"] = "{0}: {1}".format(type(exc).__name__, exc)
    return result


def summarize_results(
    *,
    started_at: str,
    ended_at: str,
    duration_sec: float,
    trace_root: str,
    report_root: str,
    results: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    rows = list(results)
    successes = [row for row in rows if row.get("success") is True]
    failures = [row for row in rows if row.get("success") is False]
    overheads = [float(row["overhead_ms"]) for row in rows if row.get("overhead_ms") is not None]
    return {
        "schema_version": "a2r2_shadow_long_run.v1",
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_sec": round(float(duration_sec), 2),
        "episodes": len(rows),
        "successes": len(successes),
        "failures": len(failures),
        "predicted_failures": sum(1 for row in rows if row.get("predicted") is True),
        "captured_not_predicted": sum(1 for row in rows if row.get("captured_not_predicted") is True),
        "over_flags": sum(int(row.get("over_flags") or 0) for row in rows),
        "a2r2_flag_steps": sum(int(row.get("a2r2_flag_steps") or 0) for row in rows),
        "gated_steps": sum(int(row.get("gated_steps") or 0) for row in rows),
        "avg_overhead_ms_per_action": round(sum(overheads) / len(overheads), 3) if overheads else None,
        "trace_root": trace_root,
        "report_root": report_root,
        "results": rows,
    }


def render_markdown(summary: Dict[str, Any]) -> str:
    lines = [
        "# A2R2 Shadow Long Run",
        "",
        "- Started: {0}".format(summary.get("started_at")),
        "- Ended: {0}".format(summary.get("ended_at")),
        "- Duration seconds: {0}".format(summary.get("duration_sec")),
        "- Episodes: {0}".format(summary.get("episodes")),
        "- Successes: {0}".format(summary.get("successes")),
        "- Failures: {0}".format(summary.get("failures")),
        "- A2R2 predicted failures early: {0}".format(summary.get("predicted_failures")),
        "- Captured but not predicted: {0}".format(summary.get("captured_not_predicted")),
        "- Over-flags: {0}".format(summary.get("over_flags")),
        "- A2R2 flag steps: {0}".format(summary.get("a2r2_flag_steps")),
        "- Gated steps: {0}".format(summary.get("gated_steps")),
        "- Avg A2R2 overhead/action ms: {0}".format(summary.get("avg_overhead_ms_per_action")),
        "",
        "| # | Case | Success | Predicted | Captured not predicted | Labels | Over-flags | A2R2 flag steps | Gated | Overhead ms | Duration sec |",
        "|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in summary.get("results", []):
        lines.append(
            "| {index} | {case} | {success} | {predicted} | {captured} | {labels} | {over} | {flags} | {gated} | {overhead} | {duration} |".format(
                index=row.get("index"),
                case=row.get("case"),
                success=row.get("success"),
                predicted=row.get("predicted"),
                captured=row.get("captured_not_predicted"),
                labels=row.get("labels") or "",
                over=row.get("over_flags"),
                flags=row.get("a2r2_flag_steps"),
                gated=row.get("gated_steps"),
                overhead=row.get("overhead_ms"),
                duration=row.get("duration_sec"),
            )
        )
    return "\n".join(lines) + "\n"


def _label_counts(report: Dict[str, Any]) -> str:
    labels = report.get("agent_reported_failure_labels") or {}
    if not isinstance(labels, dict):
        return ""
    return ";".join("{0}={1}".format(key, value) for key, value in sorted(labels.items()))


def run_long_run(
    *,
    device_id: Optional[str],
    cycles: int,
    max_cases: Optional[int],
    trace_root: str,
    report_root: str,
    reasoner_backend: str,
    planner_backend: str,
    auto_confirm: bool,
) -> Dict[str, Any]:
    adb = ADBClient(device_id=device_id)
    adb.ensure_device(timeout=10)
    plan = build_case_plan(cycles=cycles, max_cases=max_cases)
    root = Path(report_root)
    root.mkdir(parents=True, exist_ok=True)
    Path(trace_root).mkdir(parents=True, exist_ok=True)

    started = datetime.now()
    results: List[Dict[str, Any]] = []
    for index, case in enumerate(plan, start=1):
        case_id = "{0:02d}_{1}".format(index, case.name)
        case_out = root / case_id
        health = reset_device(adb)
        case_started = datetime.now()
        row: Dict[str, Any] = {
            "index": index,
            "case": case.name,
            "case_id": case_id,
            "task": case.task,
            "health": health,
            "report": str(case_out / "shadow_report.json"),
        }
        try:
            report = run_shadow(
                task=case.task,
                task_type=case.task_type,
                max_steps=case.max_steps,
                device_id=adb.device_id,
                trace_dir=trace_root,
                out_dir=str(case_out),
                reasoner_backend=reasoner_backend,
                planner_backend=planner_backend,
                auto_confirm=auto_confirm,
            )
            row.update(
                {
                    "exit_code": 0,
                    "success": report.get("final_success"),
                    "predicted": report.get("a2r2_prediction_matched_failure"),
                    "captured_not_predicted": report.get("failure_captured_not_predicted"),
                    "labels": _label_counts(report),
                    "over_flags": report.get("over_flag_count"),
                    "a2r2_flag_steps": report.get("a2r2_flag_steps"),
                    "gated_steps": report.get("gated_steps"),
                    "overhead_ms": report.get("avg_a2r2_overhead_ms_per_action"),
                }
            )
        except Exception as exc:
            row.update(
                {
                    "exit_code": 2,
                    "success": False,
                    "predicted": None,
                    "captured_not_predicted": None,
                    "labels": "runner_exception",
                    "error": "{0}: {1}".format(type(exc).__name__, exc),
                }
            )
        row["duration_sec"] = round((datetime.now() - case_started).total_seconds(), 2)
        results.append(row)
        partial = summarize_results(
            started_at=started.isoformat(),
            ended_at=datetime.now().isoformat(),
            duration_sec=(datetime.now() - started).total_seconds(),
            trace_root=trace_root,
            report_root=report_root,
            results=results,
        )
        (root / "long_run_summary.partial.json").write_text(
            json.dumps(partial, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    ended = datetime.now()
    summary = summarize_results(
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        duration_sec=(ended - started).total_seconds(),
        trace_root=trace_root,
        report_root=report_root,
        results=results,
    )
    (root / "long_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (root / "long_run_summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeated A2R2 shadow episodes against a real device.")
    parser.add_argument("--device-id", default=None, help="ADB serial. Defaults to first ready device.")
    parser.add_argument("--cycles", type=int, default=2, help="Repeat the built-in safe case plan this many times.")
    parser.add_argument("--max-cases", type=int, default=None, help="Optional cap on total episodes.")
    parser.add_argument("--reasoner-backend", default="rule", help="Page reasoner backend.")
    parser.add_argument("--planner-backend", default="rule", help="Planner backend.")
    parser.add_argument("--auto-confirm", action="store_true", help="Bypass confirmation prompts.")
    parser.add_argument("--trace-dir", default=None, help="Trace output root.")
    parser.add_argument("--out-dir", default=None, help="Report output root.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    stamp = timestamp()
    trace_root = args.trace_dir or "data/traces/a2r2_shadow_long_run_{0}".format(stamp)
    report_root = args.out_dir or "data/reports/a2r2_shadow_long_run_{0}".format(stamp)
    try:
        summary = run_long_run(
            device_id=args.device_id,
            cycles=args.cycles,
            max_cases=args.max_cases,
            trace_root=trace_root,
            report_root=report_root,
            reasoner_backend=args.reasoner_backend,
            planner_backend=args.planner_backend,
            auto_confirm=args.auto_confirm,
        )
    except ADBError as exc:
        print(json.dumps({"status": "error", "kind": "adb_error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({key: summary[key] for key in summary if key != "results"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
