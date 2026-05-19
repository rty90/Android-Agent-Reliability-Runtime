import json
import tempfile
import unittest
from pathlib import Path

from a2r2 import Observation, ProposedAction, ReliabilityRuntime, RuntimeConfig
from a2r2.reports.trace_viewer import build_view_model, write_viewer


class A2R2TraceViewerTests(unittest.TestCase):
    def test_trace_viewer_writes_process_html(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime = ReliabilityRuntime(RuntimeConfig(traces_root=str(root / "traces"), agent_name="viewer_test"))
            before = Observation(
                ui_tree_hash="before",
                package="com.example",
                metadata={"page": "home", "visible_text": ["Send"]},
            )
            after = Observation(
                ui_tree_hash="before",
                package="com.example",
                metadata={"page": "home", "visible_text": ["Send"]},
            )
            action = ProposedAction(action_type="tap", target_text="Send")
            decision = runtime.check_before_action("send a test", before, action, [])
            verification = runtime.verify_after_action("send a test", before, action, after, [])
            runtime.record_step("send a test", before, action, decision, after, verification, [])
            runtime.write_episode_summary(goal="send a test", final_success=False)

            out_path = root / "viewer.html"
            result = write_viewer(str(root / "traces"), str(out_path), latest=20)
            html = out_path.read_text(encoding="utf-8")
            model = build_view_model(str(root / "traces"), str(out_path), latest=20)

        self.assertEqual(result["episodes"], 1)
        self.assertIn("A2R2 Trace Viewer", html)
        self.assertIn("Gate Decision", html)
        self.assertIn("unsafe_action", html)
        self.assertEqual(model["totals"]["steps"], 1)
        self.assertEqual(model["episodes"][0]["steps"][0]["decision_label"], "unsafe_action")
        self.assertEqual(model["episodes"][0]["steps"][0]["diagnosis_label"], "unsafe_action")


if __name__ == "__main__":
    unittest.main()
