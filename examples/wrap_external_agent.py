from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.adapters import DummyExternalAgent


READY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.FrameLayout">
    <node text="Search" resource-id="example:id/search" clickable="true" enabled="true" />
    <node text="Delete account" resource-id="example:id/delete" clickable="true" enabled="true" />
  </node>
</hierarchy>
"""

RESULT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.FrameLayout">
    <node text="Search results" resource-id="example:id/results" clickable="false" enabled="true" />
  </node>
</hierarchy>
"""


def build_agent() -> DummyExternalAgent:
    return DummyExternalAgent(
        [
            ProposedAction(action_type="tap", target_text="Search"),
            ProposedAction(action_type="tap", target_text="Delete account"),
            ProposedAction(action_type="done", raw={"agent_claimed_success": True}),
        ]
    )


def simulate_after(before: Observation, action: ProposedAction, allowed: bool) -> Observation:
    if allowed and action.target_text == "Search":
        return Observation(
            ui_tree_hash="results",
            metadata={"xml_text": RESULT_XML, "visible_text": ["Search results"]},
        )
    return Observation(
        ui_tree_hash=before.ui_tree_hash,
        metadata=dict(before.metadata),
        screenshot_path=before.screenshot_path,
        xml_path=before.xml_path,
        activity=before.activity,
        package=before.package,
    )


def run(trace_dir: str, steps: int) -> int:
    goal = "Demonstrate an external agent wrapped by A2R2."
    runtime = ReliabilityRuntime(
        RuntimeConfig(
            traces_root=trace_dir,
            agent_name="dummy_external_agent",
            agent_meta={"example": "wrap_external_agent"},
        )
    )
    agent = build_agent()
    history: List[dict] = []
    observation = Observation(
        ui_tree_hash="ready",
        metadata={"xml_text": READY_XML, "visible_text": ["Search", "Delete account"]},
    )

    for _ in range(steps):
        action = agent.propose(goal, observation)
        decision = runtime.check_before_action(goal, observation, action, history)
        after = simulate_after(observation, action, decision.allowed)
        verification = runtime.verify_after_action(goal, observation, action, after, history)
        record = runtime.record_step(goal, observation, action, decision, after, verification, history)
        history.append(record)
        observation = after
        print(
            "step={0} action={1} decision={2} allowed={3} diagnosis={4}".format(
                record["step_index"],
                action.action_type,
                decision.decision,
                decision.allowed,
                decision.diagnosis_label or verification.diagnosis_label or "-",
            )
        )
        if decision.decision == "manual_handoff":
            break

    runtime.write_episode_summary(goal=goal, final_success=None, agent_claimed_success=False)
    print("trace={0}".format(runtime.export_trace()["episode_dir"]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap a dummy external agent with A2R2.")
    parser.add_argument("--dry-run", action="store_true", help="Run a simulated flow without Android.")
    parser.add_argument("--trace-dir", default="data/traces", help="Trace output directory.")
    parser.add_argument("--steps", type=int, default=3, help="Maximum simulated steps.")
    args = parser.parse_args()
    if not args.dry_run:
        print("Only --dry-run is implemented in the v0.1 example.")
        return 2
    return run(trace_dir=args.trace_dir, steps=args.steps)


if __name__ == "__main__":
    raise SystemExit(main())
