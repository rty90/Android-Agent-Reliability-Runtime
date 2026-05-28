import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class A2R2ComparisonDemoTests(unittest.TestCase):
    def test_comparison_demo_outputs_reports_and_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo_root = Path(__file__).resolve().parents[1]
            out_dir = root / "reports"
            trace_dir = root / "traces"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(repo_root / "scripts" / "a2r2_comparison_demo.py"),
                    "--out-dir",
                    str(out_dir),
                    "--trace-dir",
                    str(trace_dir),
                ],
                cwd=str(repo_root),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            report_path = out_dir / "comparison.json"
            self.assertTrue(report_path.exists())
            self.assertTrue((out_dir / "comparison.md").exists())
            self.assertTrue((out_dir / "comparison.html").exists())
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["schema_version"], "comparison_demo.v1")
        self.assertEqual(report["summary"]["baseline"]["unsafe_action_leaks"], 1)
        self.assertEqual(report["summary"]["a2r2"]["unsafe_action_leaks"], 0)
        self.assertEqual(report["summary"]["baseline"]["non_ready_action_leaks"], 1)
        self.assertEqual(report["summary"]["a2r2"]["non_ready_action_leaks"], 0)
        self.assertEqual(report["summary"]["baseline"]["trace_records"], 0)
        self.assertGreater(report["summary"]["a2r2"]["trace_records"], 0)
        self.assertGreaterEqual(report["summary"]["a2r2"]["false_success_detected"], 1)
        self.assertGreaterEqual(report["summary"]["a2r2"]["stuck_or_no_progress_detected"], 1)


if __name__ == "__main__":
    unittest.main()
