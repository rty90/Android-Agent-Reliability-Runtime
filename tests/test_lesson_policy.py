import unittest

from app.lesson_policy import apply_lesson_safety, evaluate_lesson_evidence


class LessonPolicyTests(unittest.TestCase):
    def test_one_off_successful_demo_becomes_candidate_not_promoted(self):
        decision = evaluate_lesson_evidence(
            goal="open chrome and search for llm",
            task_type="guided_ui_task",
            resolution_label="coach_goal_done",
            trigger_reason="coach_mode_human_demonstration",
            procedure={"steps": [{"skill": "tap", "target": "Search"}, {"skill": "type_text", "text": "llm"}]},
            verified=True,
            evidence_count=1,
        )

        self.assertEqual(decision.storage_stage, "candidate_lesson")
        self.assertTrue(decision.should_create_candidate)
        self.assertFalse(decision.should_promote)
        self.assertFalse(decision.should_auto_execute)
        self.assertIn("needs_human_approval_or_repeated_evidence", decision.reasons)

    def test_human_approved_verified_demo_can_be_promoted_but_not_auto_executed(self):
        decision = evaluate_lesson_evidence(
            goal="open chrome and search for llm",
            task_type="guided_ui_task",
            resolution_label="coach_goal_done",
            procedure={"steps": [{"skill": "tap", "target": "Search"}, {"skill": "type_text", "text": "llm"}]},
            verified=True,
            human_approved=True,
        )

        self.assertEqual(decision.storage_stage, "promoted_lesson")
        self.assertTrue(decision.should_promote)
        self.assertFalse(decision.should_auto_execute)

    def test_risky_goal_is_not_promoted_even_when_approved(self):
        decision = evaluate_lesson_evidence(
            goal="login and enter password",
            task_type="guided_ui_task",
            resolution_label="coach_goal_done",
            procedure={"steps": [{"skill": "type_text", "target": "Password", "text": "secret"}]},
            verified=True,
            human_approved=True,
            evidence_count=3,
        )

        self.assertEqual(decision.storage_stage, "candidate_lesson")
        self.assertFalse(decision.should_promote)
        self.assertIn("risky_content", decision.reasons)

    def test_apply_lesson_safety_forces_hint_mode(self):
        decision = evaluate_lesson_evidence(
            goal="open fixture",
            task_type="guided_ui_task",
            resolution_label="coach_goal_done",
            procedure={"steps": [{"skill": "tap", "target": "Continue"}]},
            verified=True,
            human_approved=True,
        )
        procedure = apply_lesson_safety(
            {
                "steps": [{"skill": "tap", "target": "Continue"}],
                "safety": {"auto_execute": True},
            },
            decision=decision,
            verified=True,
        )

        self.assertFalse(procedure["safety"]["auto_execute"])
        self.assertTrue(procedure["safety"]["needs_current_ui_validation"])
        self.assertTrue(procedure["safety"]["raw_memory_filtered"])
        self.assertEqual(procedure["safety"]["lesson_stage"], "promoted_lesson")


if __name__ == "__main__":
    unittest.main()
