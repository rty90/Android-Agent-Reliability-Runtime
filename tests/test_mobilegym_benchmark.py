import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.reports.mobilegym_benchmark import build_benchmark, render_markdown


def _summary():
    # 4 episodes covering the full TP/FN/FP/TN matrix for false_complete vs
    # a2r2_false_success.
    return {
        "schema_version": "a2r2_mobilegym_import.v1",
        "episodes_total": 4,
        "episodes_imported": 4,
        "imported": [
            {"task_id": "t1", "trial_id": 0, "mobilegym_false_complete": True,
             "a2r2_false_success": True, "mobilegym_unexpected_side_effects": False,
             "a2r2_failure_labels": ["false_success"]},
            {"task_id": "t2", "trial_id": 0, "mobilegym_false_complete": True,
             "a2r2_false_success": False, "mobilegym_unexpected_side_effects": True,
             "a2r2_failure_labels": ["no_progress"]},
            {"task_id": "t3", "trial_id": 0, "mobilegym_false_complete": False,
             "a2r2_false_success": True, "mobilegym_unexpected_side_effects": False,
             "a2r2_failure_labels": ["false_success"]},
            {"task_id": "t4", "trial_id": 0, "mobilegym_false_complete": False,
             "a2r2_false_success": False, "mobilegym_unexpected_side_effects": False,
             "a2r2_failure_labels": ["unsafe_action"]},
        ],
    }


class MobileGymBenchmarkTests(unittest.TestCase):
    def _write(self, d):
        sub = Path(d) / "run1"
        sub.mkdir()
        (sub / "mobilegym_a2r2_summary.json").write_text(json.dumps(_summary()), encoding="utf-8")
        return d

    def test_false_complete_confusion(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d)
            bench = build_benchmark(d)
        fc = bench["metrics"]["false_complete"]
        self.assertEqual(fc["ground_truth"], 2)
        self.assertEqual(fc["a2r2_detected"], 2)
        conf = fc["confusion"]
        self.assertEqual((conf["tp"], conf["fn"], conf["fp"], conf["tn"]), (1, 1, 1, 1))
        self.assertEqual(conf["recall"], 0.5)
        self.assertEqual(conf["precision"], 0.5)

    def test_other_metrics_and_no_fabrication(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d)
            bench = build_benchmark(d)
        m = bench["metrics"]
        # side effects: 1 GT (t2), 1 A2R2 detected via unsafe_action (t4) -> no overlap
        self.assertEqual(m["unexpected_side_effects"]["ground_truth"], 1)
        self.assertEqual(m["unexpected_side_effects"]["a2r2_detected"], 1)
        self.assertEqual(
            (
                m["unexpected_side_effects"]["confusion"]["tp"],
                m["unexpected_side_effects"]["confusion"]["fn"],
                m["unexpected_side_effects"]["confusion"]["fp"],
            ),
            (0, 1, 1),
        )
        # no_progress: detected from labels (t2), no MobileGym GT -> None (not fabricated)
        self.assertIsNone(m["no_progress_or_stuck"]["ground_truth"])
        self.assertEqual(m["no_progress_or_stuck"]["a2r2_detected"], 1)
        # trace coverage
        self.assertEqual(m["trace_coverage"]["ground_truth"], 4)
        self.assertEqual(m["trace_coverage"]["a2r2_detected"], 4)
        markdown = render_markdown(bench)
        self.assertIn("Benchmark v0.1", markdown)
        self.assertIn("TP 0 / FN 1 / FP 1", markdown)
        self.assertIn("Side effects vs A2R2 risk labels", markdown)

    def test_empty_inputs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as d:
            bench = build_benchmark(d)
        self.assertEqual(bench["episodes_compared"], 0)
        self.assertIn("no imported episodes yet", render_markdown(bench))

    def test_script_runs(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d)
            out = Path(d) / "bench.md"
            completed = subprocess.run(
                [sys.executable, "scripts/mobilegym_benchmark.py", "--reports-dir", d, "--out", str(out)],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(out.exists())
            self.assertTrue(out.with_suffix(".json").exists())


if __name__ == "__main__":
    unittest.main()
