import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.policies.progress_policy import ProgressPolicy
from a2r2.policies.readiness_policy import ReadinessPolicy
from a2r2.reports.scorecard import generate_scorecard


READY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.FrameLayout">
    <node text="Search" resource-id="example:id/search" clickable="true" enabled="true" />
  </node>
</hierarchy>
"""

LOADING_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node class="android.widget.ProgressBar" text="Loading" clickable="false" enabled="true" />
</hierarchy>
"""


class A2R2RuntimeTests(unittest.TestCase):
    def test_trace_recorder_writes_trace_v1_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = ReliabilityRuntime(RuntimeConfig(traces_root=temp_dir, agent_name="unit_test_agent"))
            before = Observation(ui_tree_hash="before", metadata={"xml_text": READY_XML})
            after = Observation(ui_tree_hash="after", metadata={"xml_text": READY_XML.replace("Search", "Results")})
            action = ProposedAction(action_type="tap", target_text="Search")
            decision = runtime.check_before_action("search", before, action, [])
            verification = runtime.verify_after_action("search", before, action, after, [])
            runtime.record_step("search", before, action, decision, after, verification, [])

            paths = runtime.export_trace()
            line = Path(paths["steps_path"]).read_text(encoding="utf-8").strip()
            payload = json.loads(line)

        self.assertEqual(payload["schema_version"], "trace.v1")
        self.assertEqual(payload["runtime_decision"]["decision"], "allow")
        self.assertTrue(payload["progress_verification"]["progress_made"])

    def test_readiness_policy_blocks_loading_progressbar_xml(self):
        decision = ReadinessPolicy().evaluate(
            goal="tap search",
            observation=Observation(metadata={"xml_text": LOADING_XML}),
            proposed_action=ProposedAction(action_type="tap", target_text="Search"),
            history=[],
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision, "wait")
        self.assertEqual(decision.diagnosis_label, "non_ready_action")

    def test_risk_policy_handoffs_dangerous_action_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = ReliabilityRuntime(RuntimeConfig(traces_root=temp_dir))
            decision = runtime.check_before_action(
                goal="delete an item",
                observation=Observation(ui_tree_hash="ready", metadata={"xml_text": READY_XML}),
                proposed_action=ProposedAction(action_type="tap", target_text="Delete account"),
                history=[],
            )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.decision, "manual_handoff")
        self.assertEqual(decision.diagnosis_label, "unsafe_action")

    def test_progress_policy_detects_repeated_no_progress_action(self):
        action = ProposedAction(action_type="tap", target_text="Search")
        history = []
        for index in range(2):
            history.append(
                {
                    "proposed_action": action.to_dict(),
                    "progress_verification": {"progress_made": False},
                }
            )
        verification = ProgressPolicy().evaluate(
            goal="search",
            before_observation=Observation(ui_tree_hash="same", metadata={"xml_text": READY_XML}),
            action=action,
            after_observation=Observation(ui_tree_hash="same", metadata={"xml_text": READY_XML}),
            history=history,
        )

        self.assertFalse(verification.progress_made)
        self.assertEqual(verification.diagnosis_label, "stuck_loop")

    def test_scorecard_handles_empty_trace_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            scorecard = generate_scorecard(str(Path(temp_dir) / "missing"))

        self.assertEqual(scorecard["steps_total"], 0)
        self.assertEqual(
            scorecard["metrics"]["non_ready_action_block_rate"]["value"],
            "insufficient data",
        )

    def test_wrap_external_agent_dry_run_runs_without_android(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(__file__).resolve().parents[1]
            completed = subprocess.run(
                [
                    sys.executable,
                    "examples\\wrap_external_agent.py",
                    "--dry-run",
                    "--trace-dir",
                    temp_dir,
                    "--steps",
                    "3",
                ],
                cwd=str(repo_root),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("decision=", completed.stdout)


if __name__ == "__main__":
    unittest.main()
