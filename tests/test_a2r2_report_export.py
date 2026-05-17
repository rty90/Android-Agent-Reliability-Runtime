import json
import tempfile
import unittest
from pathlib import Path

from a2r2.reports.scorecard import generate_scorecard
from scripts.export_a2r2_traces import convert_reports


class A2R2ReportExportTests(unittest.TestCase):
    def test_converts_chaos_loading_report_to_trace_v1(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_dir = root / "chaos" / "fixture_loading_state_20260517"
            report_dir.mkdir(parents=True)
            (report_dir / "report.json").write_text(
                json.dumps(
                    {
                        "case": "fixture_loading_state",
                        "status": "pass",
                        "reason": "Decision skill=wait.",
                        "goal": "wait for loading",
                        "decision": {"skill": "wait", "args": {"seconds": 2}},
                        "ui_state": {
                            "readiness": {"status": "loading", "label": "loading_state"},
                            "primary_blocker": {"type": "loading_state"},
                        },
                        "artifacts_dir": str(report_dir),
                    }
                ),
                encoding="utf-8",
            )

            out_dir = root / "traces"
            summary = convert_reports([root / "chaos"], out_dir=out_dir, latest=10)
            steps_path = Path(summary["episodes"][0]["steps_path"])
            payload = json.loads(steps_path.read_text(encoding="utf-8").strip())
            scorecard = generate_scorecard(str(out_dir))

        self.assertEqual(summary["converted"], 1)
        self.assertEqual(payload["schema_version"], "trace.v1")
        self.assertEqual(payload["runtime_decision"]["decision"], "wait")
        self.assertFalse(payload["runtime_decision"]["allowed"])
        self.assertEqual(payload["runtime_decision"]["diagnosis_label"], "non_ready_action")
        self.assertEqual(
            scorecard["metrics"]["non_ready_action_block_rate"]["value"],
            1.0,
        )

    def test_converts_long_tail_subresults_into_one_episode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            report_dir = root / "long_tail" / "long_tail_demo"
            report_dir.mkdir(parents=True)
            (report_dir / "long_tail_report.json").write_text(
                json.dumps(
                    {
                        "case": "long_tail_agent_smoke",
                        "status": "fail",
                        "passed": 1,
                        "failed": 1,
                        "results": [
                            {
                                "case": "ok_case",
                                "status": "pass",
                                "decision": {"skill": "tap", "args": {"target": "Search"}},
                                "readiness": {"status": "ready", "label": "ready"},
                            },
                            {
                                "case": "false_done",
                                "status": "pass",
                                "decision": {"skill": None},
                                "readiness": {
                                    "status": "uncertain",
                                    "label": "browser_content_not_observable",
                                },
                            },
                        ],
                        "artifacts_dir": str(report_dir),
                    }
                ),
                encoding="utf-8",
            )

            out_dir = root / "traces"
            summary = convert_reports([root / "long_tail"], out_dir=out_dir, latest=10)
            steps_path = Path(summary["episodes"][0]["steps_path"])
            lines = [json.loads(line) for line in steps_path.read_text(encoding="utf-8").splitlines()]
            scorecard = generate_scorecard(str(out_dir))

        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1]["progress_verification"]["false_success_candidate"])
        self.assertEqual(
            scorecard["metrics"]["false_success_detection_rate"]["false_success_candidates"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
