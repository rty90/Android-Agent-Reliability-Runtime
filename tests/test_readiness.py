import unittest

from app.readiness import classify_readiness


class ReadinessTests(unittest.TestCase):
    def test_chrome_url_with_only_toolbar_is_uncertain(self):
        readiness = classify_readiness(
            {
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
                "current_url": "https://m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                "current_domain": "m.youtube.com",
                "visible_text": [
                    "Web View",
                    "Open the home page",
                    "Connection is secure",
                    "m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                    "New tab",
                    "See 20 tabs",
                    "Customize and control Google Chrome",
                ],
                "possible_targets": [
                    {
                        "label": "m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                        "resource_id": "com.android.chrome:id/url_bar",
                    }
                ],
            },
            blockers=[],
        )

        self.assertEqual(readiness["status"], "uncertain")
        self.assertEqual(readiness["label"], "browser_content_not_observable")
        self.assertIn("wait", readiness["safe_actions"])

    def test_chrome_page_with_web_content_is_ready(self):
        readiness = classify_readiness(
            {
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
                "current_url": "https://m.youtube.com/results?search_query=LLM+mobile+GUI+agent+demo",
                "current_domain": "m.youtube.com",
                "visible_text": [
                    "Web View",
                    "LLM-Based GUI Agents: Bridging Human Interfaces and Autonomous AI",
                ],
                "possible_targets": [],
            },
            blockers=[],
        )

        self.assertEqual(readiness["status"], "ready")

    def test_web_challenge_is_blocked(self):
        readiness = classify_readiness(
            {
                "app": "com.android.chrome",
                "current_package": "com.android.chrome",
                "current_url": "https://stackoverflow.com/nocaptcha?s=abc",
                "current_domain": "stackoverflow.com",
                "visible_text": ["Human verification", "I'm not a robot"],
                "possible_targets": [],
            },
            blockers=[],
        )

        self.assertEqual(readiness["status"], "blocked")
        self.assertEqual(readiness["label"], "web_blocker")


if __name__ == "__main__":
    unittest.main()
