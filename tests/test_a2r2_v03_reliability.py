import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2 import Observation, ProposedAction, RuntimeConfig
from a2r2.calibration.trust import build_trust_table, render_markdown as render_trust_md
from a2r2.evidence import EvidenceVerdict, StaticEvidenceProvider, build_arbiter_prompt, parse_verdict_text
from a2r2.interventions import InterventionConfig, InterventionEngine
from a2r2.policies.progress_policy import ProgressPolicy
from a2r2.policies.readiness_policy import ReadinessPolicy
from a2r2.review.retrospect import build_review_plan, run_review, score_exam


READY_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<hierarchy><node text="Continue" resource-id="app:id/c" clickable="true" enabled="true" /></hierarchy>'
)


def _obs(ui_hash, activity="/settings", package="mobilegym.wechat", missing_after=False, xml=READY_XML):
    metadata = {"xml_text": xml, "visible_text": ["Continue"]}
    if missing_after:
        metadata["after_observation_missing"] = True
    return Observation(ui_tree_hash=ui_hash, activity=activity, package=package, metadata=metadata)


def _history_step(action_type, x, y, activity, package="mobilegym.wechat", progress=True):
    return {
        "proposed_action": {"action_type": action_type, "x": x, "y": y, "target_text": None, "target_resource_id": None},
        "before_state": {"activity": activity, "package": package, "ui_tree_hash": "h"},
        "after_state": {"ui_tree_hash": "h2"},
        "progress_verification": {"progress_made": progress},
    }


class DegenerateActionTests(unittest.TestCase):
    def test_pointer_action_without_target_or_coords_is_flagged(self):
        policy = ReadinessPolicy(RuntimeConfig())
        decision = policy.evaluate(
            goal="g", observation=_obs("h"), proposed_action=ProposedAction(action_type="tap"), history=[]
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.diagnosis_label, "degenerate_action")

    def test_normal_tap_not_flagged_as_degenerate(self):
        policy = ReadinessPolicy(RuntimeConfig())
        decision = policy.evaluate(
            goal="g", observation=_obs("h"), proposed_action=ProposedAction(action_type="tap", x=10, y=20), history=[]
        )
        self.assertNotEqual(decision.diagnosis_label, "degenerate_action")

    def test_back_without_coords_is_fine(self):
        policy = ReadinessPolicy(RuntimeConfig())
        decision = policy.evaluate(
            goal="g", observation=_obs("h"), proposed_action=ProposedAction(action_type="back"), history=[]
        )
        self.assertNotEqual(decision.diagnosis_label, "degenerate_action")


class TerminalObservationTests(unittest.TestCase):
    def test_after_missing_suppresses_no_progress(self):
        policy = ProgressPolicy(RuntimeConfig())
        before = _obs("same")
        after = _obs("same", missing_after=True)
        verification = policy.evaluate("g", before, ProposedAction(action_type="tap", x=1, y=2), after, [])
        self.assertIsNone(verification.diagnosis_label)
        self.assertIn("after_observation_missing", verification.evidence)

    def test_after_missing_preserves_false_success(self):
        policy = ProgressPolicy(RuntimeConfig())
        before = _obs("same")
        after = _obs("same", missing_after=True)
        after.metadata["goal_marker_present"] = False
        action = ProposedAction(action_type="done", raw={"agent_claimed_success": True})
        verification = policy.evaluate("g", before, action, after, [])
        self.assertTrue(verification.false_success_candidate)
        self.assertEqual(verification.diagnosis_label, "false_success")


class ScreenIdentityLoopTests(unittest.TestCase):
    def test_same_action_same_screen_repeated_is_stuck_even_with_noisy_pixels(self):
        policy = ProgressPolicy(RuntimeConfig())
        history = [
            _history_step("tap", 460, 940, "/settings"),
            _history_step("tap", 460, 940, "/settings"),
        ]
        # Pixels/hash change every step (noisy), but XML is identical and the
        # same action repeats on the same screen identity -> stuck_loop.
        before = _obs("hash_a", activity="/settings")
        after = _obs("hash_b", activity="/settings")
        verification = policy.evaluate(
            "g", before, ProposedAction(action_type="tap", x=460, y=940), after, history
        )
        self.assertEqual(verification.diagnosis_label, "stuck_loop")
        self.assertIn("same_action_same_screen_repeated", verification.evidence)

    def test_real_xml_change_is_not_stuck(self):
        policy = ProgressPolicy(RuntimeConfig())
        history = [
            _history_step("tap", 460, 940, "/settings"),
            _history_step("tap", 460, 940, "/settings"),
        ]
        before = _obs("hash_a", activity="/settings")
        after = _obs("hash_b", activity="/settings", xml=READY_XML.replace("Continue", "Changed"))
        verification = policy.evaluate(
            "g", before, ProposedAction(action_type="tap", x=460, y=940), after, history
        )
        self.assertNotEqual(verification.diagnosis_label, "stuck_loop")

    def test_loop_detected_even_on_terminal_step(self):
        # A loop's n-th repeat is often the episode's final recorded step (the
        # harness kills it there). Repetition evidence comes from history, so
        # the missing after-observation must not suppress stuck_loop.
        policy = ProgressPolicy(RuntimeConfig())
        history = [
            _history_step("tap", 460, 940, "/settings"),
            _history_step("tap", 460, 940, "/settings"),
        ]
        before = _obs("hash_a", activity="/settings")
        after = _obs("hash_a", activity="/settings", missing_after=True)
        verification = policy.evaluate(
            "g", before, ProposedAction(action_type="tap", x=460, y=940), after, history
        )
        self.assertEqual(verification.diagnosis_label, "stuck_loop")

    def test_different_screen_resets_repetition(self):
        policy = ProgressPolicy(RuntimeConfig())
        history = [
            _history_step("tap", 460, 940, "/home"),
            _history_step("tap", 460, 940, "/settings"),
        ]
        before = _obs("hash_a", activity="/settings")
        after = _obs("hash_b", activity="/settings")
        verification = policy.evaluate(
            "g", before, ProposedAction(action_type="tap", x=460, y=940), after, history
        )
        self.assertNotEqual(verification.diagnosis_label, "stuck_loop")


class MobileGymImportV03Tests(unittest.TestCase):
    def _write_run(self, root: Path, with_infra_error=False, overdue=False):
        run_dir = root / "runs" / "r1"
        episode_dir = run_dir / "trajectory" / "wechat_T"
        episode_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps({"agent": "uitars", "repeat_n": 1}), encoding="utf-8")
        results = []
        result = {
            "id": "wechat.T",
            "task_name": "T",
            "trial_id": 0,
            "is_success": False,
            "progress": 1.0 if overdue else 0.0,
            "false_complete": False,
            "execution": {"stop_reason": "MAX_STEPS", "steps": 2},
            "judge": {"success": False, "clean": True},
        }
        results.append(result)
        if with_infra_error:
            results.append(
                {
                    "id": "wechat.E",
                    "trial_id": 0,
                    "is_success": False,
                    "progress": 0,
                    "false_complete": False,
                    "execution": {"stop_reason": "ERROR", "error": "BadRequestError: context length"},
                }
            )
        (run_dir / "results.jsonl").write_text(
            "\n".join(json.dumps(r) for r in results) + "\n", encoding="utf-8"
        )
        trajectory = [
            {"step": 1, "route": {"app": "wechat", "path": "/"}, "action_type": "CLICK",
             "action_data": {"point": [10, 20]}, "thought": "t1", "screenshot": "s1.jpg"},
            {"step": 2, "route": {"app": "wechat", "path": "/me"}, "action_type": "CLICK",
             "action_data": {"point": [30, 40]}, "thought": "t2", "screenshot": "s2.jpg"},
        ]
        (episode_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
        (episode_dir / "s1.jpg").write_bytes(b"a")
        (episode_dir / "s2.jpg").write_bytes(b"b")
        return run_dir

    def test_infra_error_excluded_not_imported(self):
        from a2r2.adapters.mobilegym import import_mobilegym_run

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = self._write_run(root, with_infra_error=True)
            summary = import_mobilegym_run(run_dir, traces_root=root / "traces")
        self.assertEqual(summary["episodes_infra_error"], 1)
        self.assertEqual(summary["episodes_imported"], 1)
        self.assertEqual(summary["infra_errors"][0]["reason"], "infra_error")

    def test_terminal_step_not_labeled_no_progress_and_overdue_fields(self):
        from a2r2.adapters.mobilegym import import_mobilegym_run

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run_dir = self._write_run(root, overdue=True)
            summary = import_mobilegym_run(run_dir, traces_root=root / "traces")
            episode = summary["imported"][0]
            self.assertTrue(episode["mobilegym_overdue_termination"])
            self.assertTrue(episode["a2r2_no_termination_after_progress"])
            steps = [
                json.loads(line)
                for line in Path(episode["a2r2_steps_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            last = steps[-1]
            self.assertTrue(last["after_state"]["metadata"].get("after_observation_missing"))
            self.assertIsNone(last["progress_verification"]["diagnosis_label"])


class TrustTableTests(unittest.TestCase):
    def test_trust_table_counts_and_tiers(self):
        summary = {
            "imported": [
                {"task_id": "wechat.A", "mobilegym_success": False, "a2r2_failure_labels": ["stuck_loop"]},
                {"task_id": "wechat.B", "mobilegym_success": False, "a2r2_failure_labels": ["stuck_loop"]},
                {"task_id": "wechat.C", "mobilegym_success": True, "a2r2_failure_labels": ["no_progress"]},
            ]
        }
        with tempfile.TemporaryDirectory() as temp:
            sub = Path(temp) / "r"
            sub.mkdir()
            (sub / "mobilegym_a2r2_summary.json").write_text(json.dumps(summary), encoding="utf-8")
            table = build_trust_table(temp, min_samples=2)
        cells = {"{0}|{1}".format(c["label"], c["context"]): c for c in table["cells"]}
        stuck = cells["stuck_loop|wechat"]
        self.assertEqual(stuck["fired"], 2)
        self.assertEqual(stuck["precision_vs_episode_failure"], 1.0)
        self.assertEqual(stuck["tier"], "trusted_candidate")
        nop = cells["no_progress|wechat"]
        self.assertEqual(nop["tier"], "insufficient")
        self.assertIn("Trust Table", render_trust_md(table))


class EvidenceProviderTests(unittest.TestCase):
    def test_prompt_and_parse_roundtrip(self):
        prompt = build_arbiter_prompt("open settings", "tap(Save)", claims_done=True)
        self.assertIn("open settings", prompt)
        self.assertIn("claims the task is complete", prompt)
        verdict = parse_verdict_text('blah {"reasonable": false, "goal_evidence": "no", "reason": "wrong screen"} blah')
        self.assertIs(verdict.reasonable, False)
        self.assertIs(verdict.goal_evidence, False)
        self.assertEqual(verdict.reason, "wrong screen")

    def test_parse_garbage_is_null_verdict(self):
        verdict = parse_verdict_text("the model rambled with no json")
        self.assertIsNone(verdict.reasonable)

    def test_static_provider_records_calls(self):
        provider = StaticEvidenceProvider([EvidenceVerdict(reasonable=False, reason="x")])
        verdict = provider.assess(goal="g", action_desc="tap(a)")
        self.assertIs(verdict.reasonable, False)
        self.assertEqual(provider.calls[0]["action_desc"], "tap(a)")


class ReviewTests(unittest.TestCase):
    def _episode(self, root: Path):
        episode = root / "ep1"
        episode.mkdir()
        steps = [
            {"step_index": 1, "goal": "g", "diagnosis": {"label": None},
             "proposed_action": {"action_type": "tap", "x": 1, "y": 2}, "before_state": {}},
            {"step_index": 2, "goal": "g", "diagnosis": {"label": "stuck_loop"},
             "proposed_action": {"action_type": "tap", "target_text": "Save", "raw": {}},
             "before_state": {"screenshot_path": None}},
        ]
        (episode / "steps.jsonl").write_text(
            "\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8"
        )
        return episode

    def test_plan_selects_flagged_and_review_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            episode = self._episode(Path(temp))
            plan = build_review_plan(str(episode))
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0]["step_index"], 2)
            provider = StaticEvidenceProvider([EvidenceVerdict(reasonable=False, reason="absent")])
            review = run_review(plan, provider)
        self.assertEqual(review["first_wrong_step"], 2)
        self.assertEqual(review["steps_reviewed"], 1)

    def test_exam_scoring(self):
        provider = StaticEvidenceProvider(
            [EvidenceVerdict(reasonable=False), EvidenceVerdict(reasonable=True)]
        )
        cases = [
            {"goal": "g", "action_desc": "tap(save)", "expected_reasonable": False},
            {"goal": "g", "action_desc": "tap(ok)", "expected_reasonable": False},
        ]
        result = score_exam(cases, provider)
        self.assertEqual(result["cases"], 2)
        self.assertEqual(result["agreed"], 1)
        self.assertEqual(result["agreement_rate"], 0.5)


class InterventionEngineTests(unittest.TestCase):
    def test_degenerate_pointer_triggers_retry_with_budget(self):
        engine = InterventionEngine(goal="g", config=InterventionConfig(max_retries_per_episode=1))
        first = engine.on_proposed("click", screen_key="/a")
        self.assertEqual(first.kind, "retry")
        second = engine.on_proposed("click", screen_key="/a")
        self.assertNotEqual(second.kind, "retry")  # budget exhausted

    def test_stall_note_fires_at_second_repeat(self):
        engine = InterventionEngine(goal="g")
        self.assertEqual(engine.on_proposed("click", x=1, y=2, screen_key="/s").kind, "none")
        decision = engine.on_proposed("click", x=1, y=2, screen_key="/s")
        self.assertEqual(decision.kind, "note")
        self.assertIn("identical", decision.note)

    def test_progressing_actions_never_noted(self):
        engine = InterventionEngine(goal="g")
        self.assertEqual(engine.on_proposed("click", x=1, y=2, screen_key="/a").kind, "none")
        self.assertEqual(engine.on_proposed("click", x=3, y=4, screen_key="/b").kind, "none")
        self.assertEqual(engine.on_proposed("click", x=5, y=6, screen_key="/c").kind, "none")

    def test_early_complete_claim_verified_once(self):
        engine = InterventionEngine(goal="g")
        engine.on_proposed("click", x=1, y=2, screen_key="/only")
        decision = engine.on_proposed("done", screen_key="/only", claims_done=True)
        self.assertEqual(decision.kind, "note")
        self.assertIn("Re-check", decision.note)
        again = engine.on_proposed("done", screen_key="/only", claims_done=True)
        self.assertEqual(again.kind, "none")  # verify fires once

    def test_complete_after_real_navigation_not_questioned(self):
        engine = InterventionEngine(goal="g")
        engine.on_proposed("click", x=1, y=2, screen_key="/a")
        engine.on_proposed("click", x=3, y=4, screen_key="/b")
        decision = engine.on_proposed("done", screen_key="/b", claims_done=True)
        self.assertEqual(decision.kind, "none")


if __name__ == "__main__":
    unittest.main()
