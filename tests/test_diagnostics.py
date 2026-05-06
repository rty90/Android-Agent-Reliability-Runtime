import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.diagnostics import (
    SCHEMA_VERSION,
    build_diagnostic_report,
    summarize_for_console,
    write_failure_diagnostic,
)


SIMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Search or type here" package="com.example.chaosfixture" resource-id="input" content-desc="Search field" clickable="true" focusable="true" focused="true" enabled="true" bounds="[10,20][210,120]" class="android.widget.EditText" hint="Search or type here" />
</hierarchy>
"""


class FakeCompletedProcess(object):
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class DiagnosticADB(object):
    adb_path = "C:\\adb\\adb.exe"
    device_id = "emulator-5554"

    def list_devices(self, only_ready=False):
        return [{"device_id": "emulator-5554", "status": "device"}]

    def get_current_focus(self):
        return "mCurrentFocus=Window{abc u0 com.example.chaosfixture/.MainActivity}"

    def shell(self, command, check=True, timeout=None):
        if "dumpsys activity" in command:
            return "topResumedActivity=ActivityRecord{ com.example.chaosfixture/.MainActivity }"
        if "dumpsys window" in command:
            return ""
        if "dumpsys input_method" in command:
            return ""
        return ""

    def run(self, *args, **kwargs):
        return FakeCompletedProcess(stdout="FATAL EXCEPTION: main\njava.lang.RuntimeException: boom")

    def screenshot(self, save_path):
        path = Path(save_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    def dump_ui_xml(self, local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SIMPLE_XML, encoding="utf-8")
        return path


class DisconnectedADB(DiagnosticADB):
    device_id = "missing-device"

    def list_devices(self, only_ready=False):
        return [{"device_id": "emulator-5554", "status": "device"}]

    def screenshot(self, save_path):
        raise AssertionError("screenshot should not be attempted for a disconnected requested device")

    def dump_ui_xml(self, local_path):
        raise AssertionError("xml dump should not be attempted for a disconnected requested device")


class DiagnosticTests(unittest.TestCase):
    def test_build_report_redacts_secret_keys_and_collects_device_snapshot(self):
        adb = DiagnosticADB()

        report = build_diagnostic_report(
            kind="unit_failure",
            status="fail",
            summary="Readable failure",
            goal="open fixture",
            error={"message": "bad", "api_key": "secret"},
            adb=adb,
            context={"token": "hidden", "safe": "visible"},
        )

        self.assertEqual(report["schema_version"], SCHEMA_VERSION)
        self.assertEqual(report["human_summary"], "Readable failure")
        self.assertEqual(report["device"]["foreground_package"], "com.example.chaosfixture")
        self.assertIn("RuntimeException", report["device"]["crash_log_tail"])
        self.assertNotIn("api_key", json.dumps(report))
        self.assertNotIn("token", json.dumps(report))
        self.assertIn("visible", json.dumps(report))

    def test_write_failure_diagnostic_captures_artifacts_and_console_summary(self):
        adb = DiagnosticADB()
        with TemporaryDirectory() as temp_dir:
            report = write_failure_diagnostic(
                label="unit_failure",
                kind="unit_failure",
                summary="Something failed",
                goal="inspect fixture",
                adb=adb,
                artifacts={"extra_path": "data/tmp/example.txt"},
                output_dir=Path(temp_dir),
            )

            report_path = Path(report["artifacts"]["diagnostic_report_path"])
            self.assertTrue(report_path.exists())
            self.assertTrue(Path(report["artifacts"]["screenshot_path"]).exists())
            self.assertTrue(Path(report["artifacts"]["ui_dump_path"]).exists())
            self.assertTrue(Path(report["artifacts"]["screen_summary_path"]).exists())
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
            self.assertEqual(payload["artifacts"]["extra_path"], "data/tmp/example.txt")
            console = summarize_for_console(payload)
            self.assertIn("Something failed", console)
            self.assertIn("diagnostic.json", console)

    def test_write_failure_diagnostic_skips_artifact_capture_when_requested_device_is_offline(self):
        adb = DisconnectedADB()
        with TemporaryDirectory() as temp_dir:
            report = write_failure_diagnostic(
                label="offline",
                kind="adb_error",
                summary="Device missing",
                adb=adb,
                requested_device="missing-device",
                output_dir=Path(temp_dir),
            )

            self.assertFalse(report["device"]["connected"])
            self.assertNotIn("screenshot_path", report["artifacts"])
            self.assertNotIn("ui_dump_path", report["artifacts"])
            self.assertTrue(Path(report["artifacts"]["diagnostic_report_path"]).exists())


if __name__ == "__main__":
    unittest.main()
