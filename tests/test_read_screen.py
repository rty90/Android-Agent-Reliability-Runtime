import unittest
from pathlib import Path

from app.affordances import build_affordance_graph
from app.demo_config import build_demo_message_config
from app.skills.read_screen import detect_page_name, read_screen_summary


CHROME_BILIBILI_SEARCH_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="" package="com.android.chrome" clickable="false" bounds="[0,0][1280,2856]" class="android.widget.FrameLayout" />
  <node text="m.bilibili.com/search?keyword=Llm" resource-id="com.android.chrome:id/url_bar" package="com.android.chrome" clickable="true" focusable="true" focused="false" bounds="[240,156][836,324]" class="android.widget.EditText" hint="Search Google or type URL" />
  <node text="Why AI can't fix its own code problems" resource-id="" package="com.android.chrome" clickable="false" bounds="[531,1110][1260,1224]" class="android.widget.TextView" />
</hierarchy>
"""


class ReadScreenADB(object):
    def __init__(
        self,
        xml_text,
        focus="mCurrentFocus=Window{123 u0 com.android.chrome/.Main}",
        shell_outputs=None,
    ):
        self.xml_text = xml_text
        self.focus = focus
        self.shell_outputs = shell_outputs or {}

    def dump_ui_xml(self, local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.xml_text, encoding="utf-8")
        return path

    def get_current_focus(self):
        return self.focus

    def shell(self, command, check=True, timeout=None):
        return self.shell_outputs.get(command, "")


class ReadScreenTests(unittest.TestCase):
    def test_detect_page_name_prefers_calendar_saved_page_over_generic_search(self):
        config = build_demo_message_config()
        visible_text = [
            "com.google.android.calendar:id/drawer_layout",
            "Search",
            "Jump to Today",
            "Open Tasks",
            "Creation menu",
            "Wednesday 15 April 2026, Open Schedule View",
        ]

        page = detect_page_name(visible_text, "", config)

        self.assertEqual(page, "reminder_saved")

    def test_detect_page_name_prefers_settings_home_over_messages_search(self):
        config = build_demo_message_config()
        visible_text = [
            "com.android.settings:id/settings_homepage_container",
            "com.android.settings:id/search_action_bar",
            "Search Settings",
            "Network & internet",
            "Connected devices",
            "Apps",
            "Notifications",
            "Storage",
        ]

        page = detect_page_name(visible_text, "", config)

        self.assertEqual(page, "settings_home")

    def test_affordance_graph_exposes_click_and_type_actions(self):
        summary = {
            "app": "unknown",
            "page": "keep_editor",
            "possible_targets": [
                {
                    "target_id": "n001",
                    "label": "Note",
                    "resource_id": "com.google.android.keep:id/edit_note_text",
                    "content_desc": "",
                    "class_name": "android.widget.EditText",
                    "clickable": True,
                    "bounds": {
                        "left": 0,
                        "top": 591,
                        "right": 1280,
                        "bottom": 735,
                        "center_x": 640,
                        "center_y": 663,
                    },
                }
            ],
        }

        graph = build_affordance_graph(summary)

        action_ids = [action["action_id"] for action in graph["actions"]]
        self.assertIn("tap:n001", action_ids)
        self.assertIn("type:n001", action_ids)
        self.assertEqual(graph["actions"][0]["args"]["target_id"], "n001")

    def test_read_screen_extracts_chrome_package_url_and_bilibili_search_page(self):
        summary = read_screen_summary(
            ReadScreenADB(CHROME_BILIBILI_SEARCH_XML),
            "tmp/test_dbs/chrome_bilibili_search.xml",
            runtime_config=build_demo_message_config(),
        )

        self.assertEqual(summary["app"], "com.android.chrome")
        self.assertEqual(summary["current_package"], "com.android.chrome")
        self.assertEqual(summary["current_domain"], "m.bilibili.com")
        self.assertEqual(summary["page"], "bilibili_search_results")
        self.assertIn("keyword=Llm", summary["current_url"])

    def test_read_screen_returns_unobservable_summary_for_invalid_ui_dump(self):
        summary = read_screen_summary(
            ReadScreenADB(
                "ERROR: null root node returned by UiTestAutomationBridge.",
                focus="mCurrentFocus=Window{abc u0 com.google.android.googlequicksearchbox/.Search}",
            ),
            "tmp/test_dbs/null_root.xml",
            runtime_config=build_demo_message_config(),
        )

        self.assertEqual(summary["page"], "unobservable_screen")
        self.assertEqual(summary["current_package"], "com.google.android.googlequicksearchbox")
        self.assertIn("ParseError", summary["ui_dump_error"])
        self.assertIn("null root node", summary["xml_text"])
        self.assertEqual(summary["possible_target_total_count"], 0)

    def test_read_screen_attaches_system_overlay_probe_result(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="" package="com.android.chrome" clickable="false" bounds="[0,0][1280,2856]" class="android.widget.FrameLayout" />
  <node text="Search Google or type URL" resource-id="com.android.chrome:id/url_bar" package="com.android.chrome" clickable="true" focusable="true" focused="true" bounds="[48,156][1000,324]" class="android.widget.EditText" />
</hierarchy>
"""
        summary = read_screen_summary(
            ReadScreenADB(
                xml,
                shell_outputs={
                    "dumpsys window": "Window #7 Window{abc u0 InputMethod}\nmImeShowing=true",
                    "dumpsys input_method": (
                        "isStylusHandwritingEnabled=true\n"
                        "privateImeOptions=restrictDirectWritingArea=true"
                    ),
                },
            ),
            "tmp/test_dbs/chrome_overlay.xml",
            runtime_config=build_demo_message_config(),
        )

        self.assertTrue(summary["system_overlay"]["present"])
        self.assertTrue(summary["system_overlay"]["blocks_input"])
        self.assertEqual(summary["system_overlay"]["type"], "handwriting_input_method")

    def test_read_screen_probes_overlay_for_web_searchview_surface(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" resource-id="" package="com.android.chrome" clickable="false" bounds="[0,0][1280,2856]" class="android.widget.FrameLayout" />
  <node text="tsf" resource-id="tsf" package="com.android.chrome" clickable="false" focusable="false" focused="false" bounds="[24,1107][1257,1260]" class="android.widget.SearchView" />
</hierarchy>
"""
        summary = read_screen_summary(
            ReadScreenADB(
                xml,
                shell_outputs={
                    "dumpsys window": "Window #7 Window{abc u0 InputMethod}\nmImeShowing=true",
                    "dumpsys input_method": "isStylusHandwritingEnabled=true",
                },
            ),
            "tmp/test_dbs/chrome_searchview_overlay.xml",
            runtime_config=build_demo_message_config(),
        )

        self.assertTrue(summary["system_overlay"]["present"])
        self.assertTrue(summary["system_overlay"]["blocks_input"])
        self.assertEqual(summary["system_overlay"]["type"], "handwriting_input_method")


if __name__ == "__main__":
    unittest.main()
