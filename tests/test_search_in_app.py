import unittest

from app.skills.search_in_app import SearchInAppSkill
from app.state import AgentState


class MockSearchADB(object):
    def __init__(self):
        self.url_history = []
        self.tap_history = []
        self.input_history = []
        self.keyevents = []

    def open_url(self, url, package_name=None, wait_time=1.0):
        self.url_history.append(
            {
                "url": url,
                "package_name": package_name,
            }
        )

    def tap(self, x, y):
        self.tap_history.append((x, y))

    def input_text(self, text):
        self.input_history.append(text)

    def keyevent(self, key_code):
        self.keyevents.append(key_code)


class DummyContext(object):
    def __init__(self, adb, summary):
        self.adb = adb
        self.state = AgentState(screen_summary=summary)
        self.runtime_config = None


class SearchInAppSkillTests(unittest.TestCase):
    def test_browser_surface_prefers_intent_search(self):
        adb = MockSearchADB()
        summary = {
            "app": "com.android.chrome",
            "page": "browser_search",
            "focus": "mCurrentFocus=Window{123 u0 com.android.chrome/.Main}",
            "visible_text": ["Search or type URL"],
            "possible_targets": [
                {
                    "label": "Search or type URL",
                    "resource_id": "com.android.chrome:id/url_bar",
                    "class_name": "android.widget.EditText",
                    "clickable": True,
                    "focused": True,
                }
            ],
        }
        context = DummyContext(adb, summary)

        result = SearchInAppSkill().execute({"query": "bilibili llm", "prefer_intent": True}, context)

        self.assertTrue(result["success"])
        self.assertEqual(len(adb.url_history), 1)
        self.assertEqual(
            adb.url_history[0]["url"],
            "https://search.bilibili.com/all?keyword=llm",
        )
        self.assertEqual(adb.url_history[0]["package_name"], "com.android.chrome")
        self.assertEqual(adb.input_history, [])
        self.assertEqual(adb.tap_history, [])

    def test_web_searchview_surface_prefers_intent_search(self):
        adb = MockSearchADB()
        summary = {
            "app": "com.android.chrome",
            "page": "browser_site",
            "focus": "mCurrentFocus=Window{123 u0 com.android.chrome/.Main}",
            "visible_text": ["Google Search"],
            "possible_targets": [
                {
                    "label": "tsf",
                    "resource_id": "tsf",
                    "class_name": "android.widget.SearchView",
                    "clickable": False,
                    "focusable": False,
                    "focused": False,
                }
            ],
        }
        context = DummyContext(adb, summary)

        result = SearchInAppSkill().execute({"query": "llm"}, context)

        self.assertTrue(result["success"])
        self.assertEqual(len(adb.url_history), 1)
        self.assertEqual(adb.url_history[0]["url"], "https://www.google.com/search?q=llm")
        self.assertEqual(adb.url_history[0]["package_name"], "com.android.chrome")
        self.assertEqual(adb.input_history, [])
        self.assertEqual(adb.tap_history, [])


if __name__ == "__main__":
    unittest.main()
