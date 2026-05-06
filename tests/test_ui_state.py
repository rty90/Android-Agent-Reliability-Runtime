import unittest

from app.ui_state import normalize_ui_state


class UIStateTests(unittest.TestCase):
    def test_detects_stylus_overlay_as_input_blocker(self):
        state = normalize_ui_state(
            goal="open chrome and search for llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "browser_search",
                "visible_text": ["Search or type URL", "Try out your stylus", "Write here", "Cancel", "Next"],
                "possible_targets": [],
            },
        )

        self.assertEqual(state["primary_blocker"]["type"], "input_blocking_overlay")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["skill"], "back")
        self.assertEqual(state["goal_progress"]["stage"], "clear_blocker")

    def test_treats_system_input_overlay_as_search_input_context(self):
        state = normalize_ui_state(
            goal="open chrome and search for llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "browser_search",
                "visible_text": ["Search or type URL", "Trending searches"],
                "possible_targets": [
                    {
                        "label": "Search or type URL",
                        "class_name": "android.widget.EditText",
                        "focused": True,
                        "target_id": "n001",
                    }
                ],
                "system_overlay": {
                    "present": True,
                    "scope": "system",
                    "type": "handwriting_input_method",
                    "blocks_input": True,
                    "confidence": 0.88,
                    "recommended_recovery": "back",
                    "evidence": ["dumpsys window reports an InputMethod window"],
                },
            },
        )

        self.assertIsNone(state["primary_blocker"])
        self.assertEqual(state["input_context"]["type"], "input_method_overlay")
        self.assertEqual(state["input_context"]["suppressed_blockers"][0]["source"], "system_overlay")
        self.assertEqual(state["goal_progress"]["stage"], "enter_query")

    def test_treats_system_input_overlay_as_text_entry_context(self):
        state = normalize_ui_state(
            goal='enter "hello chaos" into the input surface',
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.example.chaosfixture",
                "page": "input_surface",
                "visible_text": ["Search or type here"],
                "possible_targets": [
                    {
                        "label": "Search or type here",
                        "class_name": "android.widget.EditText",
                        "focused": True,
                        "target_id": "n001",
                    }
                ],
                "system_overlay": {
                    "present": True,
                    "scope": "system",
                    "type": "handwriting_input_method",
                    "blocks_input": True,
                    "confidence": 0.88,
                    "recommended_recovery": "back",
                    "evidence": ["dumpsys window reports an InputMethod window"],
                },
            },
        )

        self.assertIsNone(state["primary_blocker"])
        self.assertEqual(state["input_context"]["type"], "input_method_overlay")
        self.assertEqual(state["primary_input"]["target_id"], "n001")

    def test_detects_permission_dialog_and_suggests_allow(self):
        state = normalize_ui_state(
            goal="open gmail and create a new email draft",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.permissioncontroller",
                "page": "message_thread",
                "visible_text": ["Allow Gmail to send you notifications?", "Allow", "Don\u2019t allow"],
                "possible_targets": [
                    {
                        "label": "Allow",
                        "class_name": "android.widget.Button",
                        "clickable": True,
                        "target_id": "n002",
                    },
                    {
                        "label": "Don\u2019t allow",
                        "class_name": "android.widget.Button",
                        "clickable": True,
                        "target_id": "n003",
                    },
                ],
            },
        )

        self.assertEqual(state["primary_blocker"]["type"], "permission_dialog")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["skill"], "tap")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["args"]["target_id"], "n002")

    def test_detects_blocking_dialog_and_suggests_allow(self):
        state = normalize_ui_state(
            goal="handle the blocking dialog",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.example.chaosfixture",
                "page": "chaos_fixture",
                "visible_text": ["Blocking Dialog", "This dialog blocks the current task.", "Allow", "Cancel"],
                "possible_targets": [
                    {
                        "label": "Allow",
                        "class_name": "android.widget.Button",
                        "clickable": True,
                        "target_id": "n011",
                    },
                    {
                        "label": "Cancel",
                        "class_name": "android.widget.Button",
                        "clickable": True,
                        "target_id": "n012",
                    },
                ],
            },
        )

        self.assertEqual(state["primary_blocker"]["type"], "blocking_dialog")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["skill"], "tap")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["args"]["target_id"], "n011")

    def test_detects_error_state_and_suggests_retry(self):
        state = normalize_ui_state(
            goal="recover from the error state",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.example.chaosfixture",
                "page": "chaos_fixture",
                "visible_text": ["Something went wrong", "Retry"],
                "possible_targets": [
                    {
                        "label": "Retry",
                        "class_name": "android.widget.Button",
                        "clickable": True,
                        "target_id": "n021",
                    }
                ],
            },
        )

        self.assertEqual(state["primary_blocker"]["type"], "error_state")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["skill"], "tap")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["args"]["target_id"], "n021")

    def test_detects_loading_state_and_suggests_wait(self):
        state = normalize_ui_state(
            goal="wait for the loading state to finish",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.example.chaosfixture",
                "page": "chaos_fixture",
                "visible_text": ["Loading"],
                "possible_targets": [],
            },
        )

        self.assertEqual(state["primary_blocker"]["type"], "loading_state")
        self.assertEqual(state["primary_blocker"]["suggested_action"]["skill"], "wait")

    def test_progressbar_keeps_loading_state_even_with_stale_loaded_text(self):
        state = normalize_ui_state(
            goal="wait for the loading state to finish",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.example.chaosfixture",
                "page": "chaos_fixture",
                "visible_text": ["Loading progress", "Loaded successfully"],
                "possible_targets": [
                    {
                        "label": "Loading progress",
                        "class_name": "android.widget.ProgressBar",
                        "clickable": False,
                    }
                ],
            },
        )

        self.assertEqual(state["primary_blocker"]["type"], "loading_state")

    def test_marks_gmail_compose_draft_goal_complete(self):
        state = normalize_ui_state(
            goal="open gmail and create a new email draft",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.google.android.gm",
                "page": "message_thread",
                "visible_text": ["Navigate up", "Attach files", "Send", "More options", "compose", "From"],
                "possible_targets": [],
            },
        )

        self.assertTrue(state["goal_progress"]["done"])
        self.assertEqual(state["goal_progress"]["stage"], "done")
        self.assertIn("do not tap Send", state["goal_progress"]["next_hint"])

    def test_search_surface_progress_identifies_enter_query_stage(self):
        state = normalize_ui_state(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
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
            },
        )

        self.assertEqual(state["goal_progress"]["stage"], "enter_query")
        self.assertEqual(state["primary_input"]["target_id"], "n017")
        self.assertIn("bilibili llm", state["goal_progress"]["next_hint"])

    def test_site_search_page_without_query_is_not_complete(self):
        state = normalize_ui_state(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "bilibili_site",
                "current_domain": "m.bilibili.com",
                "current_url": "https://m.bilibili.com/search",
                "visible_text": ["搜索-哔哩哔哩_Bilibili", "大家都在搜", "取消"],
                "possible_targets": [
                    {
                        "label": "m.bilibili.com/search",
                        "resource_id": "com.android.chrome:id/url_bar",
                        "class_name": "android.widget.EditText",
                        "clickable": True,
                        "focused": False,
                        "target_id": "n026",
                    }
                ],
            },
        )

        self.assertFalse(state["goal_progress"]["done"])
        self.assertEqual(state["goal_progress"]["stage"], "enter_query")

    def test_chrome_omnibox_suggestions_are_not_site_search_complete(self):
        state = normalize_ui_state(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "browser_page",
                "current_domain": "",
                "current_url": "",
                "visible_text": [
                    "llm",
                    "com.android.chrome:id/omnibox_results_container",
                    "15 suggested items in list below.",
                    "llm meaning",
                    "llm arena",
                ],
                "possible_targets": [
                    {
                        "label": "llm",
                        "resource_id": "com.android.chrome:id/url_bar",
                        "class_name": "android.widget.EditText",
                        "clickable": True,
                        "focused": True,
                        "target_id": "n006",
                    }
                ],
            },
        )

        self.assertFalse(state["goal_progress"]["done"])
        self.assertEqual(state["goal_progress"]["stage"], "enter_query")

    def test_site_search_page_with_query_is_complete(self):
        state = normalize_ui_state(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "bilibili_search_results",
                "current_domain": "m.bilibili.com",
                "current_url": "https://m.bilibili.com/search?keyword=llm",
                "visible_text": ["Llm-哔哩哔哩_Bilibili", "综合", "番剧"],
                "possible_targets": [],
            },
        )

        self.assertTrue(state["goal_progress"]["done"])
        self.assertEqual(state["goal_progress"]["stage"], "done")

    def test_browser_url_match_without_web_content_is_uncertain_not_complete(self):
        state = normalize_ui_state(
            goal="on YouTube mobile web in Chrome, find videos about LLM mobile GUI agents",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
                "page": "browser_site",
                "current_domain": "m.youtube.com",
                "current_url": "https://m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                "visible_text": [
                    "Web View",
                    "Open the home page",
                    "Connection is secure",
                    "m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                    "New tab",
                    "See 20 tabs",
                ],
                "possible_targets": [
                    {
                        "label": "m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                        "resource_id": "com.android.chrome:id/url_bar",
                        "class_name": "android.widget.EditText",
                        "clickable": True,
                        "target_id": "n008",
                    }
                ],
            },
        )

        self.assertEqual(state["readiness"]["status"], "uncertain")
        self.assertFalse(state["goal_progress"]["done"])
        self.assertEqual(state["goal_progress"]["stage"], "uncertain")


if __name__ == "__main__":
    unittest.main()
