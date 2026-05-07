import unittest

from app.overlay_detector import detect_system_overlay


WINDOW_DUMP_WITH_IME = """
Window #7 Window{abc u0 InputMethod}:
  mAttrs={(0,0)(fillxfill) gr=BOTTOM package=com.google.android.inputmethod.latin type=INPUT_METHOD}
  mHasSurface=true
mImeWindow=Window{abc u0 InputMethod}
mImeShowing=true
mCurrentFocus=Window{def u0 com.android.chrome/com.google.android.apps.chrome.Main}
"""


INPUT_METHOD_DUMP_WITH_HANDWRITING = """
Input method client state:
  mCurId=com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME
  isStylusHandwritingEnabled=true
  supportedHandwritingGestureTypes=[selectRange, deleteRange, insert]
  privateImeOptions=restrictDirectWritingArea=true
"""


INPUT_METHOD_DUMP_WITH_SUPPORTED_BUT_INACTIVE_HANDWRITING = """
Input method client state:
  mCurImeId=com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME
  supportedHandwritingGestureTypes=(none)
  isStylusHandwritingEnabled=false
InputMethod #1:
  mId=com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME
  mSupportsStylusHandwriting=true
  mSupportsConnectionlessStylusHandwriting=true
"""


class OverlayDetectorTests(unittest.TestCase):
    def test_detects_handwriting_input_method_overlay_when_input_is_active(self):
        summary = {
            "possible_targets": [
                {
                    "label": "Search Google or type URL",
                    "class_name": "android.widget.EditText",
                    "focused": True,
                    "resource_id": "com.android.chrome:id/url_bar",
                }
            ]
        }

        overlay = detect_system_overlay(
            summary,
            window_dump=WINDOW_DUMP_WITH_IME,
            input_method_dump=INPUT_METHOD_DUMP_WITH_HANDWRITING,
        )

        self.assertTrue(overlay["present"])
        self.assertTrue(overlay["blocks_input"])
        self.assertEqual(overlay["type"], "handwriting_input_method")
        self.assertEqual(overlay["recommended_recovery"], "back")
        self.assertGreaterEqual(overlay["confidence"], 0.8)
        self.assertEqual(overlay["package"], "com.google.android.inputmethod.latin")

    def test_detects_handwriting_input_method_overlay_for_web_searchview(self):
        summary = {
            "possible_targets": [
                {
                    "label": "tsf",
                    "resource_id": "tsf",
                    "class_name": "android.widget.SearchView",
                    "focused": False,
                }
            ]
        }

        overlay = detect_system_overlay(
            summary,
            window_dump=WINDOW_DUMP_WITH_IME,
            input_method_dump=INPUT_METHOD_DUMP_WITH_HANDWRITING,
        )

        self.assertTrue(overlay["present"])
        self.assertTrue(overlay["blocks_input"])
        self.assertEqual(overlay["type"], "handwriting_input_method")

    def test_ignores_input_method_window_without_app_input_target(self):
        overlay = detect_system_overlay(
            {"possible_targets": [{"label": "Trending searches", "class_name": "android.widget.TextView"}]},
            window_dump=WINDOW_DUMP_WITH_IME,
            input_method_dump=INPUT_METHOD_DUMP_WITH_HANDWRITING,
        )

        self.assertFalse(overlay["present"])
        self.assertFalse(overlay["blocks_input"])

    def test_detects_non_handwriting_ime_as_present_but_not_blocking(self):
        summary = {
            "possible_targets": [
                {
                    "label": "Search",
                    "class_name": "android.widget.EditText",
                    "focused": True,
                }
            ]
        }

        overlay = detect_system_overlay(summary, window_dump=WINDOW_DUMP_WITH_IME, input_method_dump="")

        self.assertTrue(overlay["present"])
        self.assertFalse(overlay["blocks_input"])
        self.assertEqual(overlay["type"], "input_method")

    def test_supported_but_inactive_handwriting_does_not_block(self):
        summary = {
            "possible_targets": [
                {
                    "label": "Search Google or type URL",
                    "class_name": "android.widget.EditText",
                    "focused": False,
                    "hint": "Search Google or type URL",
                }
            ]
        }

        overlay = detect_system_overlay(
            summary,
            window_dump=WINDOW_DUMP_WITH_IME,
            input_method_dump=INPUT_METHOD_DUMP_WITH_SUPPORTED_BUT_INACTIVE_HANDWRITING,
        )

        self.assertTrue(overlay["present"])
        self.assertFalse(overlay["blocks_input"])
        self.assertEqual(overlay["type"], "input_method")

    def test_active_handwriting_without_visible_ime_does_not_block(self):
        summary = {
            "possible_targets": [
                {
                    "label": "Search Google or type URL",
                    "class_name": "android.widget.EditText",
                    "focused": True,
                }
            ]
        }
        hidden_window_dump = """
Window #7 Window{abc u0 InputMethod}:
  mAttrs={(0,0)(fillxfill) package=com.google.android.inputmethod.latin type=INPUT_METHOD}
mCurrentFocus=Window{def u0 com.android.chrome/com.google.android.apps.chrome.Main}
"""

        overlay = detect_system_overlay(
            summary,
            window_dump=hidden_window_dump,
            input_method_dump=INPUT_METHOD_DUMP_WITH_HANDWRITING,
        )

        self.assertTrue(overlay["present"])
        self.assertFalse(overlay["blocks_input"])
        self.assertEqual(overlay["type"], "handwriting_input_method")

    def test_ignores_resident_notification_shade_when_focus_stays_on_app(self):
        summary = {"possible_targets": [{"label": "Home", "class_name": "android.widget.TextView"}]}
        window_dump = """
Window #6: WindowStateAnimator{a66f063 NotificationShade}
mCurrentFocus=Window{bdca2a6 u0 com.android.chrome/com.google.android.apps.chrome.Main}
mFocusedApp=ActivityRecord{144198972 u0 com.android.chrome/com.google.android.apps.chrome.Main t13}
mExpandedPanel=Window{823c553 u0 NotificationShade}
"""

        overlay = detect_system_overlay(summary, window_dump=window_dump, input_method_dump="")

        self.assertFalse(overlay["present"])
        self.assertFalse(overlay["blocks_input"])


if __name__ == "__main__":
    unittest.main()
