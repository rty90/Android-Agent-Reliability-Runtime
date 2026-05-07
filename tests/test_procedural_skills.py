import unittest

from app.procedural_skills import resolve_guided_ui_procedure
from app.ui_state import normalize_ui_state


class ProceduralSkillTests(unittest.TestCase):
    def test_resolves_permission_blocker_as_clear_primary_blocker(self):
        screen_summary = {
            "app": "com.android.permissioncontroller",
            "page": "permission_dialog",
            "visible_text": ["Allow Gmail to send you notifications?", "Allow", "Don\u2019t allow"],
            "possible_targets": [
                {
                    "label": "Allow",
                    "class_name": "android.widget.Button",
                    "clickable": True,
                    "target_id": "n002",
                }
            ],
        }
        ui_state = normalize_ui_state(
            goal="open gmail and create a new email draft",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
        )

        decision = resolve_guided_ui_procedure(
            goal="open gmail and create a new email draft",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
            ui_state=ui_state,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.name, "clear_primary_blocker")
        self.assertEqual(decision.skill, "tap")
        self.assertEqual(decision.args["target_id"], "n002")

    def test_resolves_focused_input_text_entry_with_suppressed_overlay(self):
        screen_summary = {
            "app": "com.example.chaosfixture",
            "page": "input_surface",
            "visible_text": ["Search or type here"],
            "possible_targets": [
                {
                    "label": "Search or type here",
                    "class_name": "android.widget.EditText",
                    "clickable": True,
                    "focused": True,
                    "target_id": "n015",
                }
            ],
            "system_overlay": {
                "present": True,
                "type": "handwriting_input_method",
                "blocks_input": True,
                "recommended_recovery": "back",
            },
        }
        ui_state = normalize_ui_state(
            goal='enter "hello chaos" into the input surface',
            task_type="guided_ui_task",
            screen_summary=screen_summary,
        )

        decision = resolve_guided_ui_procedure(
            goal='enter "hello chaos" into the input surface',
            task_type="guided_ui_task",
            screen_summary=screen_summary,
            ui_state=ui_state,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.name, "enter_text_into_focused_input")
        self.assertEqual(decision.skill, "type_text")
        self.assertEqual(decision.args["text"], "hello chaos")
        self.assertEqual(decision.args["target_id"], "n015")

    def test_resolves_browser_search_surface_to_search_intent(self):
        screen_summary = {
            "app": "com.android.chrome",
            "page": "browser_search",
            "visible_text": ["Search or type URL"],
            "possible_targets": [
                {
                    "label": "Search or type URL",
                    "resource_id": "com.android.chrome:id/url_bar",
                    "class_name": "android.widget.EditText",
                    "clickable": True,
                    "focused": True,
                    "target_id": "n017",
                }
            ],
        }
        ui_state = normalize_ui_state(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
        )

        decision = resolve_guided_ui_procedure(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
            ui_state=ui_state,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.name, "browser_search_via_intent")
        self.assertEqual(decision.skill, "search_in_app")
        self.assertEqual(decision.args["query"], "bilibili llm")

    def test_resolves_web_searchview_to_search_intent_without_backing_out(self):
        screen_summary = {
            "app": "com.android.chrome",
            "page": "browser_site",
            "current_domain": "google.com",
            "current_url": "https://google.com",
            "visible_text": ["Google Search", "Try out your stylus", "Cancel", "Next"],
            "possible_targets": [
                {
                    "label": "tsf",
                    "resource_id": "tsf",
                    "class_name": "android.widget.SearchView",
                    "clickable": False,
                    "focusable": False,
                    "focused": False,
                    "target_id": "n025",
                }
            ],
            "system_overlay": {
                "present": True,
                "type": "handwriting_input_method",
                "blocks_input": True,
                "recommended_recovery": "back",
            },
        }
        ui_state = normalize_ui_state(
            goal="open chrome and search for llm",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
        )

        decision = resolve_guided_ui_procedure(
            goal="open chrome and search for llm",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
            ui_state=ui_state,
        )

        self.assertIsNotNone(decision)
        self.assertEqual(decision.name, "browser_search_via_intent")
        self.assertEqual(decision.skill, "search_in_app")
        self.assertEqual(decision.args["query"], "llm")


if __name__ == "__main__":
    unittest.main()
