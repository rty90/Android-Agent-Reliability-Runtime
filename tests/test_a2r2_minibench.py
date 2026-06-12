import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.bench.minibench import (
    EXPECT_ALLOW,
    build_scenarios,
    generate_scorecard,
    run_minibench,
    _run_scenario,
)


class MiniBenchScenarioTests(unittest.TestCase):
    def test_every_scenario_meets_its_ground_truth(self):
        scenarios = build_scenarios()
        self.assertTrue(scenarios)
        with tempfile.TemporaryDirectory() as trace_dir:
            for scenario in scenarios:
                result = _run_scenario(scenario, trace_dir)
                if scenario.expectation == EXPECT_ALLOW:
                    self.assertTrue(
                        result.gate_allowed,
                        "control {0} was over-blocked: {1}/{2}".format(
                            scenario.scenario_id, result.gate_decision, result.gate_label
                        ),
                    )
                    self.assertFalse(result.over_block, scenario.scenario_id)
                else:
                    self.assertTrue(
                        result.caught,
                        "trap {0} not caught: gate={1} label={2} verify={3}".format(
                            scenario.scenario_id,
                            result.gate_decision,
                            result.gate_label,
                            result.verify_label,
                        ),
                    )
                    self.assertTrue(
                        result.label_correct,
                        "trap {0} caught with wrong label: expected={1} gate={2} verify={3}".format(
                            scenario.scenario_id,
                            scenario.expected_label,
                            result.gate_label,
                            result.verify_label,
                        ),
                    )


class MiniBenchScorecardTests(unittest.TestCase):
    def test_scorecard_perfect_on_designed_traps(self):
        with tempfile.TemporaryDirectory() as trace_dir, tempfile.TemporaryDirectory() as out_dir:
            scorecard = run_minibench(trace_dir=trace_dir, out_dir=out_dir)

        metrics = scorecard["metrics"]
        self.assertEqual(scorecard["schema_version"], "minibench.v1")
        self.assertEqual(metrics["overall_trap_detection_rate"]["value"], 1.0)
        self.assertEqual(metrics["overall_label_accuracy"]["value"], 1.0)
        self.assertEqual(metrics["unsafe_action_leakage_rate"]["value"], 0.0)
        self.assertEqual(metrics["unsafe_action_leakage_rate"]["numerator"], 0)
        self.assertEqual(metrics["false_intervention_rate"]["value"], 0.0)

    def test_denominators_match_scenario_catalogue(self):
        scenarios = build_scenarios()
        trap_counts = Counter(s.category for s in scenarios if s.expectation != EXPECT_ALLOW)
        control_count = sum(1 for s in scenarios if s.expectation == EXPECT_ALLOW)

        with tempfile.TemporaryDirectory() as trace_dir:
            results = [_run_scenario(s, trace_dir) for s in scenarios]
        scorecard = generate_scorecard(results)

        self.assertEqual(scorecard["trap_scenarios"], sum(trap_counts.values()))
        self.assertEqual(scorecard["control_scenarios"], control_count)
        # Real denominators come from the catalogue, not the runtime output.
        self.assertEqual(scorecard["metrics"]["unsafe_action_interception_rate"]["denominator"], trap_counts["unsafe_action"])
        self.assertEqual(scorecard["metrics"]["non_ready_action_block_rate"]["denominator"], trap_counts["non_ready_action"])
        for category, bucket in scorecard["per_category"].items():
            self.assertEqual(bucket["caught"], bucket["total"], category)
            self.assertEqual(bucket["label_correct"], bucket["total"], category)

    def test_repeat_scales_denominator(self):
        scenarios = build_scenarios()
        unsafe = sum(1 for s in scenarios if s.category == "unsafe_action")
        with tempfile.TemporaryDirectory() as trace_dir, tempfile.TemporaryDirectory() as out_dir:
            scorecard = run_minibench(trace_dir=trace_dir, out_dir=out_dir, repeat=2)
        self.assertEqual(scorecard["metrics"]["unsafe_action_interception_rate"]["denominator"], unsafe * 2)


class MiniBenchOutputTests(unittest.TestCase):
    def test_reports_and_traces_written(self):
        with tempfile.TemporaryDirectory() as trace_dir, tempfile.TemporaryDirectory() as out_dir:
            scorecard = run_minibench(trace_dir=trace_dir, out_dir=out_dir)
            out_path = Path(out_dir)
            self.assertTrue((out_path / "minibench.json").exists())
            self.assertTrue((out_path / "minibench.md").exists())
            self.assertTrue((out_path / "minibench.html").exists())

            episode_dirs = [p for p in Path(trace_dir).iterdir() if p.is_dir()]
            self.assertEqual(len(episode_dirs), scorecard["scenarios_total"])
            for episode in episode_dirs:
                self.assertTrue((episode / "steps.jsonl").exists(), episode.name)
                self.assertTrue((episode / "episode_summary.json").exists(), episode.name)

            payload = json.loads((out_path / "minibench.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "minibench.v1")


class MiniBenchScriptTests(unittest.TestCase):
    def test_script_runs(self):
        with tempfile.TemporaryDirectory() as trace_dir, tempfile.TemporaryDirectory() as out_dir:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "a2r2_minibench.py"),
                    "--trace-dir",
                    trace_dir,
                    "--out-dir",
                    out_dir,
                ],
                cwd=str(REPO_ROOT),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("overall_trap_detection", completed.stdout)


if __name__ == "__main__":
    unittest.main()
