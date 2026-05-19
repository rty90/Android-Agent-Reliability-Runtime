import json
import tempfile
import time
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

    def test_trace_viewer_orders_newest_live_episode_first(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            traces_root = root / "traces"

            older = ReliabilityRuntime(
                RuntimeConfig(
                    traces_root=str(traces_root),
                    episode_id="older_report_episode",
                    agent_name="existing_harness_report",
                    runtime_mode="report_conversion",
                )
            )
            before = Observation(ui_tree_hash="old", metadata={"page": "old"})
            after = Observation(ui_tree_hash="old2", metadata={"page": "old2"})
            action = ProposedAction(action_type="wait", raw={"seconds": 1})
            decision = older.check_before_action("older", before, action, [])
            verification = older.verify_after_action("older", before, action, after, [])
            older.record_step("older", before, action, decision, after, verification, [])
            older.write_episode_summary(goal="older", final_success=True)

            time.sleep(0.01)

            live = ReliabilityRuntime(
                RuntimeConfig(
                    traces_root=str(traces_root),
                    episode_id="live_smoke_episode",
                    agent_name="a2r2_live_gate_smoke",
                    runtime_mode="live_observe_gate_verify",
                )
            )
            live_before = Observation(ui_tree_hash="live", metadata={"page": "settings_home"})
            live_after = Observation(ui_tree_hash="live", metadata={"page": "settings_home"})
            live_action = ProposedAction(action_type="tap", target_text="Send")
            live_decision = live.check_before_action("live", live_before, live_action, [])
            live_verification = live.verify_after_action("live", live_before, live_action, live_after, [])
            live.record_step("live", live_before, live_action, live_decision, live_after, live_verification, [])
            live.write_episode_summary(goal="live", final_success=False)

            out_path = root / "viewer.html"
            write_viewer(str(traces_root), str(out_path), latest=20)
            html = out_path.read_text(encoding="utf-8")
            model = build_view_model(str(traces_root), str(out_path), latest=20)

        self.assertEqual(model["episodes"][0]["episode_id"], "live_smoke_episode")
        self.assertTrue(model["episodes"][0]["is_live_smoke"])
        self.assertTrue(model["episodes"][0]["latest_recorded_at"])
        self.assertIn("Live smoke", html)
        self.assertIn("Newest first", html)


if __name__ == "__main__":
    unittest.main()
