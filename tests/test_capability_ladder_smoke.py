import unittest
import random
from pathlib import Path

from scripts.capability_ladder_smoke import (
    build_ladder_levels,
    compute_max_stable_level,
    _run_endurance,
    select_levels,
    summarize_level,
)
from scripts.chaos_ui_harness import _shell_best_effort


class CapabilityLadderSmokeTests(unittest.TestCase):
    def test_select_levels_supports_all_and_specific_level(self):
        levels = build_ladder_levels()

        self.assertGreaterEqual(len(select_levels(levels, "all")), 6)
        selected = select_levels(levels, "L4")

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].level_id, "L4")

    def test_compute_max_stable_level_stops_at_first_failure(self):
        summaries = [
            {"level_id": "L1", "status": "pass"},
            {"level_id": "L2", "status": "pass"},
            {"level_id": "L3", "status": "fail"},
            {"level_id": "L4", "status": "pass"},
        ]

        self.assertEqual(compute_max_stable_level(summaries), "L2")

    def test_summarize_level_flags_false_success_risk(self):
        level = build_ladder_levels()[0]
        results = [
            {
                "case": "uncertain_complete",
                "status": "pass",
                "decision": {"skill": None},
                "readiness": {"status": "uncertain", "label": "web_content_unobservable"},
            }
        ]

        summary = summarize_level(level, results, pass_threshold=0.8)

        self.assertEqual(summary["status"], "fail")
        self.assertEqual(summary["false_success_risk"], 1)

    def test_endurance_runner_uses_short_nested_output_root(self):
        calls = {}

        def fake_run_long_tail(**kwargs):
            calls["output_root"] = kwargs["output_root"]
            return {"status": "pass", "case": "long_tail_agent_smoke"}

        import scripts.capability_ladder_smoke as ladder

        original = ladder.run_long_tail
        try:
            ladder.run_long_tail = fake_run_long_tail
            ctx = ladder.LadderContext(
                adb=object(),
                fixture_apk=None,
                skip_install=True,
                endurance_iterations=1,
                seed=7,
            )
            _run_endurance(
                ctx,
                Path("data/tmp/capability_ladder/ladder_x/L6/repeat_01_mixed_endurance"),
                random.Random(1),
            )
        finally:
            ladder.run_long_tail = original

        self.assertEqual(calls["output_root"], Path("data/tmp/capability_ladder/ladder_x/lt"))

    def test_shell_best_effort_retries_transient_adb_error(self):
        class FlakyADB(object):
            def __init__(self):
                self.calls = 0

            def shell(self, command, check=False, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("temporary adb timeout")
                return "ok"

        adb = FlakyADB()

        _shell_best_effort(adb, "pm clear com.example.chaosfixture", timeout=1, attempts=2)

        self.assertEqual(adb.calls, 2)


if __name__ == "__main__":
    unittest.main()
