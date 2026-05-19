import unittest

from scripts.a2r2_live_gate_smoke import observation_from_summary, proposed_action_from_summary


class A2R2LiveGateSmokeTests(unittest.TestCase):
    def test_observation_from_summary_preserves_paths_and_hashes_ui(self):
        observation = observation_from_summary(
            {
                "app": "com.example",
                "current_package": "com.example",
                "page": "home",
                "visible_text": ["Search", "Settings"],
                "ui_dump_path": "data/tmp/before.xml",
                "focus": "com.example/.MainActivity",
                "possible_targets": [{"label": "Search"}],
            },
            screenshot_path="data/tmp/before.png",
        )

        self.assertEqual(observation.package, "com.example")
        self.assertEqual(observation.xml_path, "data/tmp/before.xml")
        self.assertEqual(observation.screenshot_path, "data/tmp/before.png")
        self.assertTrue(observation.ui_tree_hash)
        self.assertEqual(observation.metadata["possible_target_count"], 1)

    def test_tap_first_uses_first_clickable_target(self):
        action = proposed_action_from_summary(
            "tap-first",
            {
                "possible_targets": [
                    {
                        "label": "Search",
                        "resource_id": "example:id/search",
                        "clickable": True,
                        "enabled": True,
                        "bounds": {"center_x": 10, "center_y": 20},
                    }
                ]
            },
        )

        self.assertEqual(action.action_type, "tap")
        self.assertEqual(action.x, 10)
        self.assertEqual(action.y, 20)
        self.assertEqual(action.target_text, "Search")

    def test_dangerous_send_probe_builds_risky_action(self):
        action = proposed_action_from_summary("dangerous-send", {"possible_targets": []})

        self.assertEqual(action.action_type, "tap")
        self.assertEqual(action.target_text, "Send")
        self.assertEqual(action.raw["intent"], "risk_gate_probe")


if __name__ == "__main__":
    unittest.main()
