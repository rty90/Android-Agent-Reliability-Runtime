import json
import tempfile
import unittest
from pathlib import Path

from scripts.summarize_runs import load_records, render_text_summary, summarize_records


class SummarizeRunsTests(unittest.TestCase):
    def test_loads_chaos_and_e2e_reports(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            chaos_dir = root / "chaos" / "fixture_loading_state_20260507_160419"
            chaos_dir.mkdir(parents=True)
            (chaos_dir / "report.json").write_text(
                json.dumps(
                    {
                        "case": "fixture_loading_state",
                        "status": "pass",
                        "reason": "Decision skill=wait.",
                        "decision": {"skill": "wait", "selected_backend": "procedure"},
                        "ui_state": {
                            "readiness": {"status": "loading", "label": "loading_state"},
                            "primary_blocker": {"type": "loading_state"},
                        },
                        "artifacts_dir": str(chaos_dir),
                    }
                ),
                encoding="utf-8",
            )
            e2e_dir = root / "chaos_e2e" / "fixture_input_surface_e2e_20260507_160324"
            e2e_dir.mkdir(parents=True)
            (e2e_dir / "e2e_report.json").write_text(
                json.dumps(
                    {
                        "case": "fixture_input_surface_e2e",
                        "status": "pass",
                        "reason": "Text was entered and verified.",
                        "decision": {"skill": "type_text", "selected_backend": "procedure"},
                        "artifacts_dir": str(e2e_dir),
                    }
                ),
                encoding="utf-8",
            )

            records = load_records([root], latest=10)
            summary = summarize_records(records)

        self.assertEqual(len(records), 2)
        self.assertEqual(summary["passed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["readiness_counts"]["loading"], 1)
        self.assertEqual(summary["failure_labels"]["loading_state"], 1)
        self.assertEqual(summary["false_success_risk"], 0)

    def test_summarizes_long_tail_subcases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "long_tail" / "long_tail_20260507_160442_seed_20260507"
            run_dir.mkdir(parents=True)
            (run_dir / "long_tail_report.json").write_text(
                json.dumps(
                    {
                        "case": "long_tail_agent_smoke",
                        "status": "pass",
                        "profile": "chrome_torture",
                        "passed": 1,
                        "failed": 1,
                        "results": [
                            {
                                "status": "pass",
                                "readiness": {"status": "uncertain", "label": "browser_content_not_observable"},
                                "decision": {"skill": "wait"},
                            },
                            {
                                "status": "fail",
                                "reason": "Wrong page",
                                "readiness": {"status": "blocked", "label": "wrong_page"},
                                "decision": {"skill": "tap"},
                            },
                        ],
                        "artifacts_dir": str(run_dir),
                    }
                ),
                encoding="utf-8",
            )

            records = load_records([root], latest=5)
            summary = summarize_records(records)
            rendered = render_text_summary(records)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].passed, 1)
        self.assertEqual(records[0].failed, 1)
        self.assertEqual(records[0].failure_label, "browser_content_not_observable")
        self.assertEqual(summary["false_success_risk"], 0)
        self.assertIn("long_tail_agent_smoke", rendered)
        self.assertIn("failed=1", rendered)
        self.assertIn("artifacts:", rendered)

    def test_flags_nonready_complete_as_false_success_risk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = root / "long_tail" / "long_tail_risky"
            run_dir.mkdir(parents=True)
            (run_dir / "long_tail_report.json").write_text(
                json.dumps(
                    {
                        "case": "long_tail_agent_smoke",
                        "status": "pass",
                        "passed": 1,
                        "failed": 0,
                        "results": [
                            {
                                "status": "pass",
                                "readiness": {"status": "uncertain", "label": "browser_content_not_observable"},
                                "decision": {"skill": None},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            records = load_records([root], latest=5)
            summary = summarize_records(records)

        self.assertEqual(records[0].false_success_risk, 1)
        self.assertEqual(summary["false_success_risk"], 1)

    def test_failures_only_filters_successes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pass_dir = root / "chaos" / "pass_case"
            fail_dir = root / "chaos" / "fail_case"
            pass_dir.mkdir(parents=True)
            fail_dir.mkdir(parents=True)
            (pass_dir / "report.json").write_text(
                json.dumps({"case": "pass_case", "status": "pass"}),
                encoding="utf-8",
            )
            (fail_dir / "report.json").write_text(
                json.dumps({"case": "fail_case", "status": "fail", "reason": "bad target"}),
                encoding="utf-8",
            )

            records = load_records([root], latest=10, failures_only=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].case, "fail_case")


if __name__ == "__main__":
    unittest.main()
