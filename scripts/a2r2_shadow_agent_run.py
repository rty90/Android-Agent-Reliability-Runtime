"""Run the legacy `app/` agent on a real task with A2R2 attached in shadow mode.

A2R2 observes every step and records what it *would* have decided (allow / wait /
block / manual_handoff) plus progress verification, but never blocks the agent.
After the run it writes a shadow report answering: did A2R2 flag a problem before
the task failed, and before the agent's own guards?

This script needs a connected device/emulator (it drives the real agent). For a
device-free check of the harness logic, see tests/test_a2r2_shadow_harness.py.

Example:

    python scripts\\a2r2_shadow_agent_run.py ^
      --task "open settings and inspect current page" ^
      --task-type guided_ui_task ^
      --max-steps 10 ^
      --trace-dir data\\traces\\real_agent_shadow
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.shadow import ShadowSession, render_html, render_markdown
from app.main import run_task
from app.utils.adb import ADBError


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_shadow(
    *,
    task: str,
    task_type: Optional[str],
    max_steps: int,
    device_id: Optional[str],
    trace_dir: str,
    out_dir: str,
    reasoner_backend: str,
    planner_backend: str,
    auto_confirm: bool,
) -> dict:
    session = ShadowSession(
        goal=task,
        trace_dir=trace_dir,
        agent_meta={"task_type": task_type, "reasoner_backend": reasoner_backend},
    )
    result = run_task(
        task_text=task,
        device_id=device_id,
        planner_backend=planner_backend,
        task_type_override=task_type,
        reasoner_backend=reasoner_backend,
        max_steps=max_steps,
        auto_confirm=auto_confirm,
        step_observer=session.on_step,
    )
    report = session.finalize(
        final_success=bool(result.get("success")),
        agent_claimed_success=bool(result.get("success")),
    )

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "shadow_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_path / "shadow_report.md").write_text(render_markdown(report), encoding="utf-8")
    (out_path / "shadow_report.html").write_text(render_html(report), encoding="utf-8")
    report["out_dir"] = str(out_path)
    report["agent_result_status"] = result.get("status")
    return report


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Run the legacy agent with A2R2 in shadow mode.")
    parser.add_argument("--task", required=True, help="Natural language task for the agent.")
    parser.add_argument("--task-type", default=None, help="Optional task type override (e.g. guided_ui_task).")
    parser.add_argument("--max-steps", type=int, default=10, help="Max interactive rounds.")
    parser.add_argument("--device-id", default=None, help="ADB device serial.")
    parser.add_argument("--reasoner-backend", default="rule", help="Page reasoner backend (rule/local/openai/stack).")
    parser.add_argument("--planner-backend", default="rule", help="Planner backend (rule/openai).")
    parser.add_argument("--auto-confirm", action="store_true", help="Bypass confirmation prompts.")
    parser.add_argument("--trace-dir", default="data/traces/real_agent_shadow", help="trace.v1 output dir.")
    parser.add_argument("--out-dir", default=None, help="Shadow report output dir.")
    args = parser.parse_args(argv)

    out_dir = args.out_dir or str(
        Path("data/reports") / "a2r2_shadow_{0}".format(datetime.now().strftime("%Y%m%d_%H%M%S"))
    )

    try:
        report = run_shadow(
            task=args.task,
            task_type=args.task_type,
            max_steps=args.max_steps,
            device_id=args.device_id,
            trace_dir=args.trace_dir,
            out_dir=out_dir,
            reasoner_backend=args.reasoner_backend,
            planner_backend=args.planner_backend,
            auto_confirm=args.auto_confirm,
        )
    except ADBError as exc:
        print(json.dumps({"status": "error", "kind": "adb_error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    print("shadow_report_json={0}".format(Path(out_dir) / "shadow_report.json"))
    print("shadow_report_html={0}".format(Path(out_dir) / "shadow_report.html"))
    tl = report.get("timeline", {})
    print(
        "matched_failure={0} | lead_over_failure={1} | lead_over_agent={2} | over_flags={3} | overhead_ms/action={4}".format(
            report.get("a2r2_prediction_matched_failure"),
            tl.get("a2r2_lead_over_failure"),
            tl.get("a2r2_lead_over_agent_guard"),
            report.get("over_flag_count"),
            report.get("avg_a2r2_overhead_ms_per_action"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
