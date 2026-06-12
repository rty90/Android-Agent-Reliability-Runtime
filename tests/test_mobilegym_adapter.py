import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from a2r2.adapters.mobilegym import import_mobilegym_run


class MobileGymAdapterTests(unittest.TestCase):
    def _write_fake_run(self, root: Path) -> Path:
        run_dir = root / "runs" / "20260602_120000"
        episode_dir = run_dir / "trajectory" / "wechat_ReadMyWxid"
        episode_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(
            json.dumps({"agent": "generic", "model_name": "dummy", "repeat_n": 1}),
            encoding="utf-8",
        )
        result = {
            "id": "wechat.ReadMyWxid",
            "task_name": "Read my WeChat ID",
            "suite": "wechat",
            "trial_id": 0,
            "is_success": False,
            "progress": 0.5,
            "false_complete": True,
            "execution": {"stop_reason": "COMPLETE", "steps": 2},
            "judge": {"success": False, "clean": True, "issues": [{"path": "apps.wechat.user.id"}]},
        }
        (run_dir / "results.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
        (episode_dir / "meta.json").write_text(
            json.dumps({"task_id": "wechat.ReadMyWxid", "task_name": "Read my WeChat ID", "trial_id": 0}),
            encoding="utf-8",
        )
        trajectory = [
            {
                "step": 1,
                "route": {"app": "wechat", "path": "/me"},
                "action_type": "CLICK",
                "action_data": {"point": [500, 900]},
                "thought": "open profile",
                "summary": "",
                "screenshot": "step_001.jpg",
            },
            {
                "step": 2,
                "route": {"app": "wechat", "path": "/me"},
                "action_type": "COMPLETE",
                "action_data": {"return": "done"},
                "thought": "finished",
                "summary": "",
                "screenshot": "step_002.jpg",
            },
        ]
        (episode_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
        (episode_dir / "step_001.jpg").write_bytes(b"fake-jpg-1")
        (episode_dir / "step_002.jpg").write_bytes(b"fake-jpg-2")
        return run_dir

    def test_import_mobilegym_run_writes_trace_and_false_success(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = self._write_fake_run(root)
            summary = import_mobilegym_run(
                run_dir,
                traces_root=root / "a2r2_traces",
                out_dir=root / "a2r2_reports",
            )

            self.assertEqual(summary["episodes_total"], 1)
            self.assertEqual(summary["episodes_imported"], 1)
            self.assertEqual(summary["mobilegym_false_complete"], 1)
            self.assertEqual(summary["a2r2_false_success"], 1)
            imported = summary["imported"][0]
            self.assertTrue(Path(imported["a2r2_steps_path"]).exists())
            self.assertIn("false_success", imported["a2r2_failure_labels"])
            self.assertTrue((root / "a2r2_reports" / "mobilegym_a2r2_summary.md").exists())

    def test_import_mobilegym_run_script(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            run_dir = self._write_fake_run(root)
            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/import_mobilegym_run.py",
                    "--run-dir",
                    str(run_dir),
                    "--traces-root",
                    str(root / "traces"),
                    "--out-dir",
                    str(root / "reports"),
                ],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=True,
            )

            payload = json.loads(completed.stdout)
            self.assertEqual(payload["episodes_imported"], 1)
            self.assertTrue((root / "reports" / "mobilegym_a2r2_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
