import subprocess
import unittest
from unittest import mock

from app.utils.adb import ADBClient, ADBError


class ADBClientTests(unittest.TestCase):
    @mock.patch("app.utils.adb.find_adb_path", return_value="adb")
    @mock.patch("app.utils.adb.subprocess.run")
    def test_ensure_device_requires_requested_serial(self, mock_run, _find_adb_path):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\nemulator-5554\tdevice\n",
            stderr="",
        )
        adb = ADBClient(device_id="missing-device", timeout=1)

        with self.assertRaises(ADBError):
            adb.ensure_device(timeout=0)

    @mock.patch("app.utils.adb.find_adb_path", return_value="adb")
    @mock.patch("app.utils.adb.subprocess.run")
    def test_ensure_device_auto_selects_first_ready_device(self, mock_run, _find_adb_path):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["adb", "devices"],
            returncode=0,
            stdout="List of devices attached\nemulator-5554\tdevice\n",
            stderr="",
        )
        adb = ADBClient(timeout=1)

        self.assertEqual(adb.ensure_device(timeout=1), "emulator-5554")
        self.assertEqual(adb.device_id, "emulator-5554")


if __name__ == "__main__":
    unittest.main()
