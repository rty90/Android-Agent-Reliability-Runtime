import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from a2r2.adapters.legacy_app import (
    is_action_skill,
    observation_from_summary,
    proposed_action_from_step,
)
from a2r2.shadow import ShadowSession, render_html, render_markdown

READY_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<hierarchy><node class=\"android.widget.FrameLayout\">"
    '<node text="Continue" resource-id="app:id/continue" clickable="true" enabled="true" />'
    "</node></hierarchy>"
)
DISABLED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    "<hierarchy><node class=\"android.widget.FrameLayout\">"
    '<node text="Continue" resource-id="app:id/continue" clickable="false" enabled="true" />'
    "</node></hierarchy>"
)


def _summary(ui_hash, xml, page="home", target="Continue"):
    return {
        "app": "demo",
        "page": page,
        "ui_dump_path": "data/tmp/none.xml",
        "xml_text": xml,
        "visible_text": [target],
        "possible_targets": [{"label": target, "clickable": True}],
        "current_package": "com.demo",
    }


def _event(skill, before, after, success=True, step_index=1, detail="", args=None):
    return {
        "step_index": step_index,
        "skill": skill,
        "args": args if args is not None else {"target": "Continue"},
        "before_summary": before,
        "after_summary": after,
        "success": success,
        "detail": detail,
        "data": {},
    }


class AdapterTests(unittest.TestCase):
    def test_proposed_action_mapping(self):
        action = proposed_action_from_step("tap", {"target": "Send"})
        self.assertEqual(action.action_type, "tap")
        self.assertEqual(action.target_text, "Send")
        self.assertEqual(action.raw["agent_skill"], "tap")

    def test_confirm_action_carries_confirmation_context(self):
        action = proposed_action_from_step("confirm_action", {})
        self.assertTrue(action.raw.get("confirmation_context"))

    def test_is_action_skill(self):
        self.assertTrue(is_action_skill("tap"))
        self.assertFalse(is_action_skill("read_screen"))

    def test_observation_forwards_inline_xml(self):
        obs = observation_from_summary(_summary("h", READY_XML))
        self.assertEqual(obs.metadata["xml_text"], READY_XML)
        self.assertTrue(obs.ui_tree_hash)

    def test_observation_forwards_possible_target_facts(self):
        obs = observation_from_summary(_summary("h", READY_XML, target="Save"))
        self.assertEqual(obs.metadata["possible_target_count"], 1)
        self.assertEqual(obs.metadata["possible_targets"][0]["label"], "Save")

    def test_observation_handles_none(self):
        obs = observation_from_summary(None)
        self.assertIsNotNone(obs.ui_tree_hash)


class ShadowSessionTests(unittest.TestCase):
    def test_failing_episode_flagged_early(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="reach the target", trace_dir=trace_dir)
            disabled = _summary("disabled", DISABLED_XML)
            # Step 1: A2R2 would block (non-ready), agent taps anyway and "succeeds".
            session.on_step(_event("tap", disabled, disabled, success=True, step_index=1))
            # Step 2: the agent now fails outright.
            session.on_step(_event("tap", disabled, disabled, success=False, step_index=2))
            report = session.finalize(final_success=False)

        tl = report["timeline"]
        self.assertEqual(tl["first_failure_step"], 2)
        self.assertEqual(tl["a2r2_first_flag_step"], 1)
        self.assertEqual(tl["a2r2_lead_over_failure"], 1)
        self.assertTrue(report["a2r2_prediction_matched_failure"])
        self.assertEqual(report["schema_version"], "shadow_report.v1")

    def test_agent_failure_captured_but_not_predicted(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="type into the field", trace_dir=trace_dir)
            # A2R2 sees a ready screen and would allow; the agent then reports an
            # input-effect failure category that A2R2's current rules do not
            # predict early.
            before = _summary("ready", READY_XML, page="messages_search")
            after = _summary("changed", READY_XML.replace("Continue", "Other"), page="messages_search")
            session.on_step(
                _event(
                    "type_text",
                    before,
                    after,
                    success=False,
                    step_index=1,
                    detail="Text input did not change the UI after using shell_input.",
                    args={"text": "hello"},
                )
            )
            report = session.finalize(final_success=False)

        # A2R2 did NOT predict early...
        self.assertFalse(report["a2r2_prediction_matched_failure"])
        # ...but shadow captured the real failure category, separately.
        self.assertTrue(report["failure_captured_not_predicted"])
        self.assertEqual(report["agent_reported_failure_labels"], {"input_no_effect": 1})
        self.assertEqual(report["rows"][0]["agent_reported_failure_label"], "input_no_effect")

    def test_target_missing_is_predicted_from_ui_facts(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="create a reminder", trace_dir=trace_dir)
            before = _summary("ready", READY_XML, page="messages_search", target="Search")
            after = _summary("changed", READY_XML.replace("Continue", "Other"), page="messages_search")
            session.on_step(
                _event(
                    "tap",
                    before,
                    after,
                    success=False,
                    step_index=1,
                    detail="Unable to find tap target: save",
                    args={"target": "save"},
                )
            )
            report = session.finalize(final_success=False)

        self.assertTrue(report["a2r2_prediction_matched_failure"])
        self.assertFalse(report["failure_captured_not_predicted"])
        self.assertEqual(report["rows"][0]["would_label"], "target_missing")
        self.assertTrue(report["rows"][0]["a2r2_flagged"])

    def test_over_flag_on_successful_episode(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="benign", trace_dir=trace_dir)
            disabled = _summary("disabled", DISABLED_XML)
            changed = _summary("changed", READY_XML.replace("Continue", "Done"), target="Done")
            # A2R2 would block, but the agent step succeeded and the episode succeeds.
            session.on_step(_event("tap", disabled, changed, success=True, step_index=1))
            report = session.finalize(final_success=True)

        self.assertEqual(report["over_flag_count"], 1)
        self.assertIsNone(report["a2r2_prediction_matched_failure"])

    def test_no_progress_block_is_not_counted_as_over_flag(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="maybe false success", trace_dir=trace_dir)
            ready = _summary("ready", READY_XML, target="Search")
            session.on_step(
                _event(
                    "tap",
                    ready,
                    ready,
                    success=True,
                    step_index=1,
                    args={"target": "Save"},
                    detail="Tapped target Save.",
                )
            )
            report = session.finalize(final_success=True)

        self.assertEqual(report["rows"][0]["would_label"], "target_missing")
        self.assertEqual(report["rows"][0]["verify_label"], "no_progress")
        self.assertEqual(report["over_flag_count"], 0)

    def test_overhead_and_traces_written(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="g", trace_dir=trace_dir)
            ready = _summary("ready", READY_XML)
            session.on_step(_event("tap", ready, ready, success=True))
            report = session.finalize(final_success=True)

            self.assertIsInstance(report["avg_a2r2_overhead_ms_per_action"], float)
            self.assertGreaterEqual(report["avg_a2r2_overhead_ms_per_action"], 0.0)
            episode_dir = Path(report["episode_dir"])
            self.assertTrue((episode_dir / "steps.jsonl").exists())
            self.assertTrue((episode_dir / "episode_summary.json").exists())

    def test_non_action_skill_not_gated(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="g", trace_dir=trace_dir)
            ready = _summary("ready", READY_XML)
            session.on_step(_event("read_screen", ready, ready, success=True))
            report = session.finalize(final_success=True)
        self.assertEqual(report["gated_steps"], 0)
        self.assertEqual(report["steps_total"], 1)

    def test_observer_never_raises_on_bad_event(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="g", trace_dir=trace_dir)
            session.on_step(None)  # malformed
            report = session.finalize(final_success=None)
        self.assertTrue(report["errors"])  # captured, not raised

    def test_renderers_run(self):
        with tempfile.TemporaryDirectory() as trace_dir:
            session = ShadowSession(goal="g", trace_dir=trace_dir)
            session.on_step(_event("tap", _summary("r", READY_XML), _summary("r2", READY_XML)))
            report = session.finalize(final_success=True)
        self.assertIn("Shadow Run Report", render_markdown(report))
        self.assertIn("<html", render_html(report))


class ExecutorObserverHookTests(unittest.TestCase):
    def test_observer_receives_step_and_never_breaks_agent(self):
        from app.executor import Executor
        from app.planner import PlanStep
        from app.state import AgentState

        captured = []
        state = AgentState()
        state.screen_summary = {"page": "after_page"}
        executor = Executor(
            adb=None,
            state=state,
            logger=None,
            screenshot_manager=None,
            skill_registry={},
            step_observer=lambda event: captured.append(event),
        )
        executor._notify_step_observer(
            PlanStep("tap", {"target": "X"}),
            {"page": "before_page"},
            {"success": True, "detail": "ok", "data": {}},
            1,
        )
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["skill"], "tap")
        self.assertEqual(captured[0]["before_summary"], {"page": "before_page"})
        self.assertEqual(captured[0]["after_summary"], {"page": "after_page"})

        # A raising observer must not propagate.
        boom = Executor(
            adb=None,
            state=state,
            logger=None,
            screenshot_manager=None,
            skill_registry={},
            step_observer=lambda event: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        boom._notify_step_observer(PlanStep("tap", {}), {}, {"success": True}, 1)  # should not raise

    def test_observer_receives_exception_step_before_reraise(self):
        from app.executor import Executor
        from app.planner import PlanStep
        from app.state import AgentState

        captured = []
        state = AgentState()
        state.screen_summary = {"page": "after_exception"}
        executor = Executor(
            adb=None,
            state=state,
            logger=None,
            screenshot_manager=None,
            skill_registry={},
            step_observer=lambda event: captured.append(event),
        )

        def raise_inner(step, context, step_index):
            raise ValueError("skill exploded")

        executor._execute_step_inner = raise_inner
        with self.assertRaises(ValueError):
            executor._execute_step(PlanStep("tap", {"target": "X"}), None, 7)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["skill"], "tap")
        self.assertFalse(captured[0]["success"])
        self.assertEqual(captured[0]["data"]["exception_type"], "ValueError")
        self.assertIn("Executor exception: ValueError", captured[0]["detail"])


if __name__ == "__main__":
    unittest.main()
