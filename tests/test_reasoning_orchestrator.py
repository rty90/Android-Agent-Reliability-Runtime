import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.reasoning_orchestrator import ReasoningOrchestrator
from app.reasoning_validator import ReasoningValidator
from app.trace_bus import TraceBus


class FakeRuntime(object):
    def __init__(self, cloud_configured=False):
        self.cloud_configured_value = cloud_configured

    def ensure_local_text_service(self):
        return {"available": True, "started": False, "base_url": "http://127.0.0.1:9000/v1"}

    def ensure_local_vl_service(self):
        return {"available": True, "started": False, "base_url": "http://127.0.0.1:9001/v1"}

    def local_vl_enabled(self):
        return True

    def cloud_reviewer_configured(self):
        return self.cloud_configured_value

    def cloud_reviewer_base_url(self):
        return "https://dashscope.aliyuncs.com/compatible-mode/v1" if self.cloud_configured_value else ""

    def cloud_reviewer_api_key(self):
        return "test-key" if self.cloud_configured_value else ""

    def cloud_reviewer_model(self):
        return "qwen3.5-plus" if self.cloud_configured_value else ""

    def shutdown_owned_processes(self):
        return None


class ReasoningOrchestratorTests(unittest.TestCase):
    def _build_orchestrator(self, cloud_configured=False):
        trace_file = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl")
        trace_file.close()
        validator = ReasoningValidator(allowed_task_types=["guided_ui_task", "read_current_screen"])
        trace_bus = TraceBus(trace_path=trace_file.name, console_enabled=False)
        orchestrator = ReasoningOrchestrator(
            validator=validator,
            model_runtime=FakeRuntime(cloud_configured=cloud_configured),
            trace_bus=trace_bus,
            rule_fallback=lambda **kwargs: {
                "page_type": kwargs["screen_summary"].get("page", "unknown_page"),
                "summary": "Rule fallback selected.",
                "facts": [],
                "targets": [],
                "next_action": None,
                "confidence": 0.61,
                "requires_confirmation": False,
            },
        )
        return orchestrator, Path(trace_file.name)

    def test_invalid_local_text_can_fall_back_to_vl(self):
        orchestrator, trace_path = self._build_orchestrator()
        screenshot = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        screenshot.write(b"fakepng")
        screenshot.close()

        orchestrator._call_openai_compatible_text = lambda **kwargs: "not json"
        orchestrator._call_openai_compatible_vl = lambda **kwargs: json.dumps(
            {
                "decision": "execute",
                "task_type": "guided_ui_task",
                "skill": "tap",
                "args": {"target": "Create a note"},
                "confidence": 0.83,
                "requires_confirmation": False,
                "reason_summary": "VL found the note button.",
            }
        )

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={"page": "keep_home", "visible_text": ["Create a note"], "possible_targets": []},
            screenshot_path=screenshot.name,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "local_vl")
        self.assertEqual(result["decision"].skill, "tap")
        events = trace_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("cloud_review.start" in line for line in events))
        self.assertTrue(any("local_vl.result" in line for line in events))

    def test_invalid_local_text_without_screenshot_uses_rule_fallback(self):
        orchestrator, trace_path = self._build_orchestrator()
        orchestrator._call_openai_compatible_text = lambda **kwargs: "not json"

        result = orchestrator.resolve(
            goal="read the current screen and summarize it",
            task_type="read_current_screen",
            screen_summary={"page": "keep_home", "visible_text": ["ZX-2048"], "possible_targets": []},
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertTrue(result["decision"].fallback_used)
        events = trace_path.read_text(encoding="utf-8")
        self.assertNotIn("not json", events)

    def test_local_text_timeout_uses_rule_fallback(self):
        orchestrator, trace_path = self._build_orchestrator()

        def raise_timeout(**kwargs):
            raise TimeoutError("local text timed out")

        orchestrator._call_openai_compatible_text = raise_timeout

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={"page": "keep_home", "visible_text": ["Create a note"], "possible_targets": []},
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertTrue(result["decision"].fallback_used)
        events = trace_path.read_text(encoding="utf-8")
        self.assertIn("local_text.result", events)
        self.assertIn("validation.failed", events)

    def test_local_text_timeout_disables_future_local_attempts(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def raise_timeout(**kwargs):
            calls["local"] += 1
            raise TimeoutError("local text timed out")

        def fake_cloud(**kwargs):
            calls["cloud"] += 1
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": "tap",
                    "args": {"target": "Create a note", "target_key": "new_note"},
                    "confidence": 0.84,
                    "requires_confirmation": False,
                    "reason_summary": "Cloud review found the note button.",
                }
            )

        orchestrator._call_openai_compatible_text = raise_timeout
        orchestrator._call_openai_compatible_review = fake_cloud

        first = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={"page": "keep_home", "visible_text": ["Create a note"], "possible_targets": []},
            screenshot_path="missing.png",
            recent_actions=[],
            relevant_memories=[],
        )
        second = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={"page": "keep_home", "visible_text": ["Create a note"], "possible_targets": []},
            screenshot_path="missing.png",
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(first["decision"].selected_backend, "cloud_reviewer")
        self.assertEqual(second["decision"].selected_backend, "cloud_reviewer")
        self.assertEqual(calls["local"], 1)
        self.assertEqual(calls["cloud"], 2)

    def test_cloud_review_uses_screenshot_when_available(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        screenshot = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        screenshot.write(b"fakepng")
        screenshot.close()

        orchestrator._call_openai_compatible_text = lambda **kwargs: "not json"
        calls = {"vl": 0}

        def fake_review(**kwargs):
            if kwargs.get("screenshot_path") == screenshot.name:
                calls["vl"] += 1
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": "tap",
                    "args": {"target": "Create a note", "target_key": "new_note"},
                    "confidence": 0.84,
                    "requires_confirmation": False,
                    "reason_summary": "Cloud review found the note button.",
                }
            )

        orchestrator._call_openai_compatible_review = fake_review

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={"page": "keep_home", "visible_text": ["Create a note"], "possible_targets": []},
            screenshot_path=screenshot.name,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "cloud_reviewer")
        self.assertEqual(calls["vl"], 1)

    def test_cloud_review_skips_screenshot_for_text_model(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        orchestrator.model_runtime.cloud_reviewer_model = lambda: "qwen-plus"
        screenshot = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        screenshot.write(b"fakepng")
        screenshot.close()
        calls = {"text": 0, "vl": 0}

        def fake_text(**kwargs):
            calls["text"] += 1
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": None,
                    "args": {},
                    "confidence": 0.91,
                    "requires_confirmation": False,
                    "reason_summary": "Settings is open and visible.",
                }
            )

        def fail_vl(**kwargs):
            calls["vl"] += 1
            raise AssertionError("text-only cloud models should not receive screenshots")

        orchestrator._call_openai_compatible_text = fake_text
        orchestrator._call_openai_compatible_vl = fail_vl

        result = orchestrator.resolve(
            goal="open settings and inspect the current page",
            task_type="guided_ui_task",
            screen_summary={"page": "settings_home", "visible_text": ["Search Settings"], "possible_targets": []},
            screenshot_path=screenshot.name,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "cloud_reviewer")
        self.assertEqual(calls["text"], 1)
        self.assertEqual(calls["vl"], 0)

    def test_read_only_guided_task_uses_cloud_before_local(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("read-only guided tasks should not wait for local text first")

        def fake_review(**kwargs):
            calls["cloud"] += 1
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": None,
                    "args": {},
                    "confidence": 0.92,
                    "requires_confirmation": False,
                    "reason_summary": "Settings is open and visible.",
                }
            )

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fake_review

        result = orchestrator.resolve(
            goal="open settings and inspect the current page",
            task_type="guided_ui_task",
            screen_summary={"page": "settings_home", "visible_text": ["Search Settings"], "possible_targets": []},
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "cloud_reviewer")
        self.assertIsNone(result["decision"].skill)
        self.assertEqual(calls["cloud"], 1)
        self.assertEqual(calls["local"], 0)

    def test_read_only_guided_task_suppresses_model_action(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)

        def fake_review(**kwargs):
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": "open_app",
                    "args": {"app_name": "Settings"},
                    "confidence": 0.93,
                    "requires_confirmation": False,
                    "reason_summary": "Settings is already open.",
                }
            )

        orchestrator._call_openai_compatible_review = fake_review

        result = orchestrator.resolve(
            goal="open settings and inspect the current page",
            task_type="guided_ui_task",
            screen_summary={"page": "settings_home", "visible_text": ["Search Settings"], "possible_targets": []},
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "cloud_reviewer")
        self.assertIsNone(result["decision"].skill)
        self.assertEqual(result["decision"].args, {})

    def test_disabled_local_vl_is_skipped(self):
        orchestrator, _trace_path = self._build_orchestrator()
        orchestrator.model_runtime.local_vl_enabled = lambda: False
        orchestrator._call_openai_compatible_text = lambda **kwargs: "not json"

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={"page": "keep_home", "visible_text": ["Create a note"], "possible_targets": []},
            screenshot_path="missing.png",
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "rule")

    def test_memory_shortcut_bypasses_model_calls(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("local text should not run when a shortcut matches")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("cloud review should not run when a shortcut matches")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={
                "page": "keep_home",
                "visible_text": ["Create a note"],
                "possible_targets": [{"label": "Create a note", "clickable": True}],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
            normalized_context={
                "goal": "open keep and create a note",
                "task_type": "guided_ui_task",
                "screen_summary": {"page": "keep_home", "visible_text": ["Create a note"]},
                "recent_actions": [],
                "relevant_memories": [],
                "ui_shortcut": {
                    "skill": "tap",
                    "args": {"target": "Create a note", "target_key": "new_note"},
                    "confidence": 0.98,
                },
            },
        )

        self.assertEqual(result["decision"].selected_backend, "memory_rule")
        self.assertEqual(result["decision"].skill, "tap")
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_affordance_graph_uses_cloud_model_before_memory_shortcut(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"cloud": 0, "local": 0}

        def fake_cloud(**kwargs):
            calls["cloud"] += 1
            payload = kwargs["payload"]
            self.assertIn("affordance_graph", payload)
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": "tap",
                    "args": {
                        "action_id": "tap:n010",
                        "target_id": "n010",
                        "target": "Create a note",
                    },
                    "confidence": 0.91,
                    "requires_confirmation": False,
                    "reason_summary": "Choose the Create a note affordance.",
                }
            )

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("local text should not run before model-first cloud affordance selection")

        orchestrator._call_openai_compatible_review = fake_cloud
        orchestrator._call_openai_compatible_text = fail_local

        screen_summary = {
            "page": "keep_home",
            "visible_text": ["Create a note"],
            "possible_targets": [
                {"target_id": "n010", "label": "Create a note", "clickable": True}
            ],
            "affordance_graph": {
                "page": "keep_home",
                "actions": [
                    {
                        "action_id": "tap:n010",
                        "skill": "tap",
                        "args": {"target_id": "n010", "target": "Create a note"},
                    }
                ],
            },
        }

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary=screen_summary,
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
            normalized_context={
                "goal": "open keep and create a note",
                "task_type": "guided_ui_task",
                "screen_summary": {"page": "keep_home", "visible_text": ["Create a note"]},
                "affordance_graph": screen_summary["affordance_graph"],
                "ui_shortcut": {
                    "skill": "tap",
                    "args": {"target": "Sort notes", "target_key": "new_note"},
                    "confidence": 0.98,
                },
            },
        )

        self.assertEqual(result["decision"].selected_backend, "cloud_reviewer")
        self.assertEqual(result["decision"].args["target_id"], "n010")
        self.assertEqual(calls["cloud"], 1)
        self.assertEqual(calls["local"], 0)

    def test_keep_create_note_stops_once_editor_is_open(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed once the editor is open")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed once the editor is open")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open keep and create a note",
            task_type="guided_ui_task",
            screen_summary={
                "page": "keep_editor",
                "visible_text": ["Title", "Note"],
                "possible_targets": [
                    {"label": "Title", "resource_id": "editable_title", "clickable": True},
                    {"label": "Note", "resource_id": "edit_note_text", "clickable": True},
                ],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
            normalized_context={
                "goal": "open keep and create a note",
                "task_type": "guided_ui_task",
                "screen_summary": {"page": "keep_editor", "visible_text": ["Title", "Note"]},
                "recent_actions": [],
                "relevant_memories": [],
                "ui_shortcut": {
                    "skill": "tap",
                    "args": {"target": "Title", "target_key": "editable_title"},
                    "confidence": 0.95,
                },
            },
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertIsNone(result["decision"].skill)
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_keep_create_note_with_requested_text_types_in_editor(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed to type requested editor text")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed to type requested editor text")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open keep and create a note, then type 'agent smoke test'",
            task_type="guided_ui_task",
            screen_summary={
                "page": "keep_editor",
                "visible_text": ["Title", "Note"],
                "possible_targets": [
                    {
                        "label": "Note",
                        "resource_id": "com.google.android.keep:id/edit_note_text",
                        "class_name": "android.widget.EditText",
                        "clickable": True,
                        "target_id": "n013",
                    }
                ],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
            normalized_context={
                "goal": "open keep and create a note, then type 'agent smoke test'",
                "task_type": "guided_ui_task",
                "screen_summary": {"page": "keep_editor", "visible_text": ["Title", "Note"]},
                "recent_actions": [],
                "relevant_memories": [],
            },
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertEqual(result["decision"].skill, "type_text")
        self.assertEqual(result["decision"].args["text"], "agent smoke test")
        self.assertEqual(result["decision"].args["target_id"], "n013")
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_focused_browser_search_prefers_search_intent_before_model_calls(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when a focused search input is already available")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when a focused search input is already available")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "page": "messages_search",
                "visible_text": ["Search or type URL"],
                "affordance_graph": {
                    "actions": [
                        {
                            "action_id": "type:n017",
                            "skill": "type_text",
                            "args": {"target_id": "n017", "target": "Search or type URL"},
                        }
                    ]
                },
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
            screenshot_path=None,
            recent_actions=[{"action": "tap", "success": True, "detail": "Tapped target Search or type URL."}],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "procedure")
        self.assertEqual(result["decision"].skill, "search_in_app")
        self.assertEqual(result["decision"].args["query"], "bilibili llm")
        self.assertTrue(result["decision"].args["prefer_intent"])
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_stylus_overlay_on_focused_search_continues_search_intent(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when a focused search input is already available")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when a focused search input is already available")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "browser_search",
                "visible_text": [
                    "Search or type URL",
                    "Try out your stylus",
                    "Write here",
                    "Reset",
                    "Cancel",
                    "Next",
                ],
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
            screenshot_path=None,
            recent_actions=[{"action": "tap", "success": True, "detail": "Tapped target Search or type URL."}],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "procedure")
        self.assertEqual(result["decision"].skill, "search_in_app")
        self.assertEqual(result["decision"].args["query"], "bilibili llm")
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_permission_blocker_prefers_allow_before_model_calls(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when a permission blocker is visible")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when a permission blocker is visible")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
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
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "procedure")
        self.assertEqual(result["decision"].skill, "tap")
        self.assertEqual(result["decision"].args["target_id"], "n002")
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_loading_blocker_prefers_wait_before_model_calls(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when a loading state is visible")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when a loading state is visible")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="wait for the loading state to finish",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.example.chaosfixture",
                "page": "chaos_fixture",
                "visible_text": ["Loading"],
                "possible_targets": [],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "procedure")
        self.assertEqual(result["decision"].skill, "wait")
        self.assertEqual(result["decision"].args["seconds"], 2)
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_gmail_compose_progress_stops_before_send(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when Gmail draft is already open")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when Gmail draft is already open")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open gmail and create a new email draft",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.google.android.gm",
                "page": "message_thread",
                "visible_text": ["Navigate up", "Attach files", "Send", "More options", "com.google.android.gm:id/compose", "From"],
                "possible_targets": [
                    {
                        "label": "Send",
                        "resource_id": "com.google.android.gm:id/send",
                        "class_name": "android.widget.Button",
                        "clickable": True,
                        "target_id": "n005",
                    }
                ],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertIsNone(result["decision"].skill)
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_browser_search_results_mark_goal_complete(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when the browser search goal is already complete")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when the browser search goal is already complete")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "app": "com.android.chrome",
                "page": "browser_results",
                "visible_text": [
                    "llm-哔哩哔哩_Bilibili",
                    "m.bilibili.com/search?keyword=llm",
                ],
                "possible_targets": [],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertIsNone(result["decision"].skill)
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_browser_site_search_url_marks_goal_complete_even_with_garbled_text(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"local": 0, "cloud": 0}

        def fail_local(**kwargs):
            calls["local"] += 1
            raise AssertionError("no model call needed when the URL proves the search is complete")

        def fail_cloud(**kwargs):
            calls["cloud"] += 1
            raise AssertionError("no cloud call needed when the URL proves the search is complete")

        orchestrator._call_openai_compatible_text = fail_local
        orchestrator._call_openai_compatible_review = fail_cloud

        result = orchestrator.resolve(
            goal="open chrome and open bilibili website search llm",
            task_type="guided_ui_task",
            screen_summary={
                "page": "bilibili_search_results",
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
                "current_domain": "m.bilibili.com",
                "current_url": "https://m.bilibili.com/search?keyword=Llm",
                "visible_text": ["鍝斿摡鍝斿摡", "乱码 title"],
                "possible_targets": [],
            },
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "rule")
        self.assertIsNone(result["decision"].skill)
        self.assertEqual(calls["local"], 0)
        self.assertEqual(calls["cloud"], 0)

    def test_browser_site_search_page_without_query_does_not_mark_goal_complete(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)

        result = orchestrator.resolve(
            goal="open chrome and open bilibili website search llm",
            task_type="guided_ui_task",
            screen_summary={
                "page": "bilibili_site",
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
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
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertNotEqual(result["decision"].reason_summary, "The requested guided UI task is already complete on the current page.")
        self.assertIsNotNone(result["decision"].skill)

    def test_chrome_omnibox_suggestions_do_not_mark_site_search_goal_complete(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)

        result = orchestrator.resolve(
            goal="open chrome, open bilibili, and find videos about llm",
            task_type="guided_ui_task",
            screen_summary={
                "page": "browser_page",
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
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
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertNotEqual(result["decision"].reason_summary, "The requested guided UI task is already complete on the current page.")
        self.assertIsNotNone(result["decision"].skill)

    def test_interaction_pattern_is_disabled_for_guided_ui_tasks(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=False)
        calls = {"local": 0}

        def fake_local(**kwargs):
            calls["local"] += 1
            return json.dumps(
                {
                    "decision": "execute",
                    "task_type": "guided_ui_task",
                    "skill": "type_text",
                    "args": {
                        "target_id": "n017",
                        "action_id": "type:n017",
                        "target": "Search or type URL",
                        "text": "bilibili llm",
                        "press_enter": True,
                    },
                    "confidence": 0.86,
                    "requires_confirmation": False,
                    "reason_summary": "Local text selected the focused browser input.",
                }
            )

        orchestrator._call_openai_compatible_text = fake_local

        with mock.patch.dict("os.environ", {"AGENT_ENABLE_GUIDED_UI_MEMORY_EXPANSION": ""}):
            result = orchestrator.resolve(
                goal="open chrome, open bilibili, and find videos about llm",
                task_type="guided_ui_task",
                screen_summary={
                    "page": "messages_search",
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
                screenshot_path=None,
                recent_actions=[{"action": "tap", "success": True, "detail": "Tapped target Search or type URL."}],
                relevant_memories=[],
                normalized_context={
                    "goal": "open chrome, open bilibili, and find videos about llm",
                    "task_type": "guided_ui_task",
                    "screen_summary": {"page": "messages_search", "visible_text": ["Search or type URL"]},
                    "recent_actions": [{"action": "tap", "success": True, "detail": "Tapped target Search or type URL."}],
                    "relevant_memories": [],
                    "interaction_pattern": {
                        "skill": "type_text",
                        "args": {
                            "target_id": "n017",
                            "action_id": "type:n017",
                            "target": "Search or type URL",
                            "text": "bilibili llm",
                            "press_enter": True,
                            "dismiss_overlays_first": True,
                        },
                        "confidence": 0.96,
                    },
                },
            )

        self.assertNotEqual(result["decision"].selected_backend, "interaction_pattern")
        self.assertEqual(result["decision"].selected_backend, "procedure")
        self.assertEqual(result["decision"].skill, "search_in_app")
        self.assertEqual(result["decision"].args["query"], "bilibili llm")
        self.assertEqual(calls["local"], 0)

    def test_interaction_pattern_reenabled_for_guided_ui_when_opted_in(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=False)
        calls = {"local": 0}

        def fake_local(**kwargs):
            calls["local"] += 1
            return "{}"

        orchestrator._call_openai_compatible_text = fake_local

        with mock.patch.dict("os.environ", {"AGENT_ENABLE_GUIDED_UI_MEMORY_EXPANSION": "1"}):
            result = orchestrator.resolve(
                goal="open chrome, open bilibili, and find videos about llm",
                task_type="guided_ui_task",
                screen_summary={
                    "page": "messages_search",
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
                screenshot_path=None,
                recent_actions=[{"action": "tap", "success": True, "detail": "Tapped target Search or type URL."}],
                relevant_memories=[],
                normalized_context={
                    "goal": "open chrome, open bilibili, and find videos about llm",
                    "task_type": "guided_ui_task",
                    "screen_summary": {"page": "messages_search", "visible_text": ["Search or type URL"]},
                    "recent_actions": [{"action": "tap", "success": True, "detail": "Tapped target Search or type URL."}],
                    "relevant_memories": [],
                    "interaction_pattern": {
                        "skill": "type_text",
                        "args": {
                            "target_id": "n017",
                            "action_id": "type:n017",
                            "target": "Search or type URL",
                            "text": "bilibili llm",
                            "press_enter": True,
                            "dismiss_overlays_first": True,
                        },
                        "confidence": 0.96,
                    },
                },
            )

        self.assertEqual(result["decision"].selected_backend, "interaction_pattern")
        self.assertEqual(result["decision"].skill, "type_text")
        self.assertEqual(result["decision"].args["text"], "bilibili llm")
        self.assertEqual(calls["local"], 0)

    def test_readiness_gate_waits_on_unobservable_browser_content(self):
        orchestrator, _trace_path = self._build_orchestrator(cloud_configured=True)
        calls = {"cloud": 0}

        def fake_cloud(**kwargs):
            calls["cloud"] += 1
            return "{}"

        orchestrator._call_openai_compatible_review = fake_cloud

        result = orchestrator.resolve(
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
            screenshot_path=None,
            recent_actions=[],
            relevant_memories=[],
        )

        self.assertEqual(result["decision"].selected_backend, "readiness")
        self.assertEqual(result["decision"].skill, "wait")
        self.assertEqual(calls["cloud"], 0)


if __name__ == "__main__":
    unittest.main()
