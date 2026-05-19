from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.policies.common import stable_hash
from app.demo_config import build_demo_message_config
from app.skills.read_screen import read_screen_summary
from app.utils.adb import ADBClient, ADBError


def observation_from_summary(summary: Mapping[str, Any], screenshot_path: Optional[str] = None) -> Observation:
    xml_path = str(summary.get("ui_dump_path") or "") or None
    visible_text = list(summary.get("visible_text") or [])[:50]
    fingerprint = stable_hash(
        {
            "package": summary.get("current_package") or summary.get("app"),
            "page": summary.get("page"),
            "visible_text": visible_text[:20],
            "current_url": summary.get("current_url"),
            "system_overlay": summary.get("system_overlay"),
        }
    )
    return Observation(
        screenshot_path=screenshot_path,
        xml_path=xml_path,
        activity=str(summary.get("focus") or "") or None,
        package=str(summary.get("current_package") or summary.get("app") or "") or None,
        ui_tree_hash=fingerprint,
        metadata={
            "app": summary.get("app"),
            "page": summary.get("page"),
            "current_url": summary.get("current_url"),
            "current_domain": summary.get("current_domain"),
            "visible_text": visible_text,
            "system_overlay": summary.get("system_overlay"),
            "possible_target_count": len(summary.get("possible_targets") or []),
        },
    )


def proposed_action_from_summary(mode: str, summary: Mapping[str, Any]) -> ProposedAction:
    normalized = str(mode or "wait").strip().lower()
    if normalized == "dangerous-send":
        return ProposedAction(
            action_type="tap",
            target_text="Send",
            raw={"source": "a2r2_live_gate_smoke", "intent": "risk_gate_probe"},
        )
    if normalized == "done":
        return ProposedAction(
            action_type="done",
            raw={"agent_claimed_success": True, "source": "a2r2_live_gate_smoke"},
        )
    if normalized == "tap-first":
        target = _first_clickable_target(summary)
        return ProposedAction(
            action_type="tap",
            x=_bounds_value(target, "center_x"),
            y=_bounds_value(target, "center_y"),
            target_text=str(target.get("label") or "") or None,
            target_resource_id=str(target.get("resource_id") or target.get("target_id") or "") or None,
            raw={"source": "a2r2_live_gate_smoke", "target": target},
        )
    return ProposedAction(
        action_type="wait",
        raw={"seconds": 1.0, "source": "a2r2_live_gate_smoke"},
    )


def run_live_smoke(
    *,
    goal: str,
    proposal: str,
    out_dir: str,
    adb_path: Optional[str] = None,
    device_id: Optional[str] = None,
    execute_wait: bool = True,
) -> Dict[str, Any]:
    adb = ADBClient(adb_path=adb_path, device_id=device_id)
    selected_device = adb.ensure_device(timeout=10)
    run_dir = Path("data/tmp/a2r2_live")
    run_dir.mkdir(parents=True, exist_ok=True)

    before = capture_observation(adb, run_dir, "before")
    action = proposed_action_from_summary(proposal, before["summary"])
    runtime = ReliabilityRuntime(
        RuntimeConfig(
            traces_root=out_dir,
            agent_name="a2r2_live_gate_smoke",
            runtime_mode="live_observe_gate_verify",
            agent_meta={"device_id": selected_device, "proposal_mode": proposal},
        )
    )
    history: List[Dict[str, Any]] = []
    decision = runtime.check_before_action(goal, before["observation"], action, history)

    executed = False
    if decision.allowed and action.action_type == "wait" and execute_wait:
        time.sleep(float((action.raw or {}).get("seconds") or 1.0))
        executed = True

    after = capture_observation(adb, run_dir, "after")
    verification = runtime.verify_after_action(
        goal,
        before["observation"],
        action,
        after["observation"],
        history,
    )
    record = runtime.record_step(
        goal,
        before["observation"],
        action,
        decision,
        after["observation"],
        verification,
        history,
    )
    runtime.write_episode_summary(
        goal=goal,
        final_success=None,
        agent_claimed_success=bool(action.raw and action.raw.get("agent_claimed_success")),
    )
    return {
        "device_id": selected_device,
        "goal": goal,
        "proposal": action.to_dict(),
        "decision": decision.to_dict(),
        "executed": executed,
        "verification": verification.to_dict(),
        "trace": runtime.export_trace(),
        "recorded_step": record.get("step_index"),
        "before_page": before["summary"].get("page"),
        "after_page": after["summary"].get("page"),
    }


def capture_observation(adb: ADBClient, run_dir: Path, prefix: str) -> Dict[str, Any]:
    screenshot_path = run_dir / "{0}.png".format(prefix)
    xml_path = run_dir / "{0}.xml".format(prefix)
    adb.screenshot(str(screenshot_path))
    summary = read_screen_summary(
        adb,
        str(xml_path),
        runtime_config=build_demo_message_config(),
    )
    return {
        "summary": summary,
        "observation": observation_from_summary(summary, screenshot_path=str(screenshot_path)),
    }


def _first_clickable_target(summary: Mapping[str, Any]) -> Dict[str, Any]:
    for target in summary.get("possible_targets") or []:
        if isinstance(target, Mapping) and target.get("clickable") and target.get("enabled", True):
            return dict(target)
    for target in summary.get("possible_targets") or []:
        if isinstance(target, Mapping):
            return dict(target)
    return {}


def _bounds_value(target: Mapping[str, Any], key: str) -> Optional[int]:
    bounds = target.get("bounds") if isinstance(target.get("bounds"), Mapping) else {}
    value = bounds.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Run one live A2R2 gate/verify smoke against the current Android screen.")
    parser.add_argument("--goal", default="A2R2 live smoke: observe the current screen safely.")
    parser.add_argument(
        "--proposal",
        choices=("wait", "tap-first", "dangerous-send", "done"),
        default="wait",
        help="Synthetic external-agent proposal to gate. Only wait is executed.",
    )
    parser.add_argument("--out", default="data/traces", help="A2R2 trace output directory.")
    parser.add_argument("--adb-path", default=None, help="Optional adb.exe path.")
    parser.add_argument("--device-id", default=None, help="Optional ADB device serial.")
    parser.add_argument("--no-execute-wait", action="store_true", help="Record without sleeping for wait actions.")
    args = parser.parse_args(argv)

    try:
        result = run_live_smoke(
            goal=args.goal,
            proposal=args.proposal,
            out_dir=args.out,
            adb_path=args.adb_path,
            device_id=args.device_id,
            execute_wait=not args.no_execute_wait,
        )
    except ADBError as exc:
        print(json.dumps({"status": "error", "kind": "adb_error", "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"status": "ok", **result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
