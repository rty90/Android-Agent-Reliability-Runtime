import importlib.util
import sys
import unittest
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "a2r2_shadow_long_run.py"
    spec = importlib.util.spec_from_file_location("a2r2_shadow_long_run", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class A2R2ShadowLongRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_module()

    def test_build_case_plan_cycles_and_cap(self):
        plan = self.module.build_case_plan(cycles=3, max_cases=7)

        self.assertEqual(len(plan), 7)
        self.assertEqual(plan[0].name, "settings_inspect")
        self.assertEqual(plan[4].name, "chrome_emulator_fix")
        self.assertEqual(plan[5].name, "settings_inspect")

    def test_summarize_results_counts_shadow_signals(self):
        summary = self.module.summarize_results(
            started_at="start",
            ended_at="end",
            duration_sec=12.345,
            trace_root="traces",
            report_root="reports",
            results=[
                {
                    "success": True,
                    "predicted": None,
                    "captured_not_predicted": False,
                    "over_flags": 0,
                    "a2r2_flag_steps": 0,
                    "gated_steps": 1,
                    "overhead_ms": 1.0,
                },
                {
                    "success": False,
                    "predicted": True,
                    "captured_not_predicted": False,
                    "over_flags": 0,
                    "a2r2_flag_steps": 1,
                    "gated_steps": 2,
                    "overhead_ms": 3.0,
                },
            ],
        )

        self.assertEqual(summary["episodes"], 2)
        self.assertEqual(summary["successes"], 1)
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["predicted_failures"], 1)
        self.assertEqual(summary["captured_not_predicted"], 0)
        self.assertEqual(summary["gated_steps"], 3)
        self.assertEqual(summary["avg_overhead_ms_per_action"], 2.0)

    def test_render_markdown_includes_key_metrics(self):
        summary = self.module.summarize_results(
            started_at="start",
            ended_at="end",
            duration_sec=1,
            trace_root="traces",
            report_root="reports",
            results=[],
        )
        markdown = self.module.render_markdown(summary)

        self.assertIn("A2R2 Shadow Long Run", markdown)
        self.assertIn("Episodes: 0", markdown)
        self.assertIn("Avg A2R2 overhead/action ms", markdown)


if __name__ == "__main__":
    unittest.main()
