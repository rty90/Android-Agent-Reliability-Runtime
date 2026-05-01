import unittest
from pathlib import Path
from unittest import mock

from app.demo_config import build_demo_message_config
from app.executor import Executor
from app.memory import SQLiteMemory
from app.page_reasoner import PageReasoner
from app.planner import ExecutionPlan, PlanStep
from app.skills import build_skill_registry
from app.skills.base import SkillContext
from app.skills.manual_intervention import ManualInterventionSkill
from app.skills.type_text import TypeTextSkill
from app.state import AgentState
from app.utils.logger import setup_logger
from app.utils.screenshot import ScreenshotManager


BLOCKED_BEFORE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Search or type URL" resource-id="com.android.chrome:id/url_bar" content-desc="" clickable="true" focusable="true" focused="true" enabled="true" bounds="[100,150][900,260]" class="android.widget.EditText" hint="Search or type URL" />
  <node text="Try out your stylus" resource-id="" content-desc="" clickable="false" focusable="false" focused="false" enabled="true" bounds="[120,1200][900,1280]" class="android.widget.TextView" hint="" />
  <node text="Write here" resource-id="" content-desc="" clickable="true" focusable="true" focused="false" enabled="true" bounds="[180,1780][900,1880]" class="android.widget.EditText" hint="Write here" />
  <node text="Cancel" resource-id="" content-desc="Cancel" clickable="true" focusable="true" focused="false" enabled="true" bounds="[500,2100][760,2200]" class="android.widget.Button" hint="" />
  <node text="Next" resource-id="" content-desc="Next" clickable="true" focusable="true" focused="false" enabled="true" bounds="[820,2100][1080,2200]" class="android.widget.Button" hint="" />
</hierarchy>
"""

BLOCKED_AFTER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="llm-哔哩哔哩_Bilibili" resource-id="" content-desc="" clickable="false" focusable="true" focused="true" enabled="true" bounds="[0,324][1280,2784]" class="android.webkit.WebView" hint="" />
  <node text="llm" resource-id="" content-desc="" clickable="true" focusable="true" focused="false" enabled="true" bounds="[138,369][996,429]" class="android.widget.EditText" hint="Search" />
  <node text="综合" resource-id="" content-desc="综合" clickable="true" focusable="false" focused="false" enabled="true" bounds="[66,498][165,603]" class="android.view.View" hint="" />
</hierarchy>
"""

INPUT_SURFACE_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Search or type here" package="com.example.chaosfixture" resource-id="com.example.chaosfixture:id/edit_text_input_surface" content-desc="Search or type here" clickable="true" focusable="true" focused="true" enabled="true" bounds="[72,144][1208,280]" class="android.widget.EditText" hint="Search or type here" />
</hierarchy>
"""

INPUT_SURFACE_FILLED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="hello chaos" package="com.example.chaosfixture" resource-id="com.example.chaosfixture:id/edit_text_input_surface" content-desc="Search or type here" clickable="true" focusable="true" focused="true" enabled="true" bounds="[72,144][1208,280]" class="android.widget.EditText" hint="Search or type here" />
</hierarchy>
"""


class ManualADB(object):
    def __init__(self):
        self.current_screen = "blocked_before"
        self.device_id = "emulator-5554"
        self.xml_map = {
            "blocked_before": BLOCKED_BEFORE_XML,
            "blocked_after": BLOCKED_AFTER_XML,
        }
        self.input_history = []

    def dump_ui_xml(self, local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.xml_map[self.current_screen], encoding="utf-8")
        return path

    def get_current_focus(self):
        return "mCurrentFocus=Window{123 u0 com.android.chrome/.Main}"

    def screenshot(self, save_path):
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mock")
        return path

    def input_text_best_effort(self, text):
        self.input_history.append(text)
        return "shell_input"

    def tap(self, x, y):
        return None

    def back(self):
        return None

    def keyevent(self, key_code):
        return None


class BlockingImeADB(object):
    def __init__(self):
        self.device_id = "emulator-5554"
        self.overlay_active = True
        self.text_applied = False
        self.input_history = []
        self.back_count = 0

    def dump_ui_xml(self, local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            INPUT_SURFACE_FILLED_XML if self.text_applied else INPUT_SURFACE_EMPTY_XML,
            encoding="utf-8",
        )
        return path

    def get_current_focus(self):
        return "mCurrentFocus=Window{123 u0 com.example.chaosfixture/.MainActivity}"

    def shell(self, command, check=True, timeout=None):
        if "dumpsys window" in command and self.overlay_active:
            return "Window #1 InputMethod package=com.google.android.inputmethod.latin mImeShowing=true"
        if "dumpsys input_method" in command and self.overlay_active:
            return "isStylusHandwritingEnabled=true\nsupportedHandwritingGestureTypes=SELECT|INSERT"
        return ""

    def input_text_best_effort(self, text):
        self.input_history.append(text)
        if not self.overlay_active:
            self.text_applied = True
        return "shell_input"

    def tap(self, x, y):
        return None

    def back(self):
        self.back_count += 1
        self.overlay_active = False

    def keyevent(self, key_code):
        return None


class ManualInterventionTests(unittest.TestCase):
    def _build_memory(self, name):
        db_dir = Path("tmp/test_dbs")
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / name
        if db_path.exists():
            db_path.unlink()
        return SQLiteMemory(db_path=str(db_path))

    def test_manual_intervention_skill_captures_episode_and_persists_memory(self):
        adb = ManualADB()
        memory = self._build_memory("manual_intervention_skill.db")
        state = AgentState(current_task="open chrome and search bilibili llm", task_type="guided_ui_task")
        state.screen_summary = {"page": "browser_search", "visible_text": ["Search or type URL"]}
        context = SkillContext(
            adb=adb,
            state=state,
            logger=setup_logger(name="test-manual-skill"),
            screenshot_manager=ScreenshotManager(base_dir="data/screenshots/test"),
            registry=build_skill_registry(),
            memory=memory,
            runtime_config=build_demo_message_config(),
        )

        def user_fix(_prompt):
            adb.current_screen = "blocked_after"
            return ""

        with mock.patch("builtins.input", side_effect=user_fix):
            result = ManualInterventionSkill().execute({"reason": "overlay blocked input"}, context)

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["manual_intervention"]["resolution_label"], "dismiss_overlay")
        self.assertEqual(
            result["data"]["manual_intervention"]["reflection"]["corrected_strategy"],
            "Detect blocking overlay text/buttons first, then dismiss or complete the overlay before retrying the original action.",
        )
        episodes = memory.list_manual_interventions(limit=5)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["resolution_label"], "dismiss_overlay")
        reflections = memory.list_manual_reflections(limit=5)
        self.assertEqual(len(reflections), 1)
        self.assertFalse(reflections[0]["reflection"]["should_auto_execute"])

    def test_type_text_retries_after_blocking_input_method_overlay(self):
        adb = BlockingImeADB()
        state = AgentState(current_task='enter "hello chaos" into the input surface', task_type="guided_ui_task")
        context = SkillContext(
            adb=adb,
            state=state,
            logger=setup_logger(name="test-type-text-overlay-retry"),
            screenshot_manager=ScreenshotManager(base_dir="data/screenshots/test"),
            registry=build_skill_registry(),
            memory=self._build_memory("type_text_overlay_retry.db"),
            runtime_config=build_demo_message_config(),
        )

        result = TypeTextSkill().execute(
            {"text": "hello chaos", "target_id": "n001"},
            context,
        )

        self.assertTrue(result["success"])
        self.assertEqual(adb.input_history, ["hello chaos", "hello chaos"])
        self.assertEqual(adb.back_count, 1)
        self.assertEqual(result["data"]["overlay_recovery"], "back_then_retry")

    def test_executor_falls_back_to_manual_intervention_for_stuck_type_text(self):
        adb = ManualADB()
        memory = self._build_memory("manual_intervention_executor.db")
        logger = setup_logger(name="test-manual-executor")
        state = AgentState()
        executor = Executor(
            adb=adb,
            state=state,
            logger=logger,
            screenshot_manager=ScreenshotManager(base_dir="data/screenshots/test"),
            skill_registry=build_skill_registry(),
            memory=memory,
            context_builder=None,
            page_reasoner=PageReasoner(backend="rule"),
            runtime_config=build_demo_message_config(),
        )
        plan = ExecutionPlan(
            goal="open chrome and search bilibili llm",
            task_type="guided_ui_task",
            steps=[PlanStep("type_text", {"text": "bilibili llm", "target_id": "n001"})],
        )

        def user_fix(_prompt):
            adb.current_screen = "blocked_after"
            return ""

        with mock.patch("builtins.input", side_effect=user_fix):
            result = executor.execute_plan(plan)

        self.assertTrue(result["success"])
        self.assertEqual(result["steps"][0]["action"], "manual_intervention")
        episodes = memory.list_manual_interventions(limit=5)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["resolution_label"], "dismiss_overlay")
        reflections = memory.list_manual_reflections(limit=5)
        self.assertEqual(len(reflections), 1)
        self.assertEqual(reflections[0]["failed_skill"], "type_text")
        self.assertEqual(reflections[0]["failed_args"]["text"], "bilibili llm")

    def test_executor_falls_back_to_manual_intervention_for_onboarding_loop(self):
        adb = ManualADB()
        memory = self._build_memory("manual_intervention_onboarding_loop.db")
        logger = setup_logger(name="test-manual-onboarding-loop")
        state = AgentState(current_task="open gmail and create a draft", task_type="guided_ui_task")
        state.current_page = "messages_search"
        state.screen_summary = {
            "page": "messages_search",
            "visible_text": ["TAKE ME TO GMAIL", "OK"],
        }
        state.recent_actions = [
            {
                "action": "tap",
                "success": True,
                "detail": "Tapped target TAKE ME TO GMAIL.",
                "data": {"screen_summary": {"page": "messages_search"}},
            },
            {
                "action": "read_screen",
                "success": True,
                "detail": "Screen summary refreshed.",
                "data": {"screen_summary": {"page": "messages_search"}},
            },
            {
                "action": "tap",
                "success": True,
                "detail": "Tapped target OK.",
                "data": {"screen_summary": {"page": "messages_search"}},
            },
            {
                "action": "read_screen",
                "success": True,
                "detail": "Screen summary refreshed.",
                "data": {"screen_summary": {"page": "messages_search"}},
            },
        ]
        executor = Executor(
            adb=adb,
            state=state,
            logger=logger,
            screenshot_manager=ScreenshotManager(base_dir="data/screenshots/test"),
            skill_registry=build_skill_registry(),
            memory=memory,
            context_builder=None,
            page_reasoner=PageReasoner(backend="rule"),
            runtime_config=build_demo_message_config(),
        )

        def user_fix(_prompt):
            adb.current_screen = "blocked_after"
            return ""

        with mock.patch("builtins.input", side_effect=user_fix):
            result = executor._execute_step(PlanStep("tap", {"target": "TAKE ME TO GMAIL"}), executor._context(), 1)

        self.assertTrue(result["success"])
        self.assertEqual(result["action"], "manual_intervention")
        episodes = memory.list_manual_interventions(limit=5)
        self.assertEqual(len(episodes), 1)
        reflections = memory.list_manual_reflections(limit=5)
        self.assertEqual(len(reflections), 1)
        self.assertEqual(reflections[0]["failed_skill"], "tap")


if __name__ == "__main__":
    unittest.main()
