from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import time
from urllib.parse import quote
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ADBError(RuntimeError):
    """Raised when an adb command cannot be completed successfully."""


def find_adb_path(explicit_path: Optional[str] = None) -> str:
    """Resolve the adb executable path from common locations."""
    candidates = []

    if explicit_path:
        candidates.append(explicit_path)

    adb_in_path = shutil.which("adb")
    if adb_in_path:
        candidates.append(adb_in_path)

    for env_name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        env_value = os.environ.get(env_name)
        if env_value:
            candidates.append(str(Path(env_value) / "platform-tools" / "adb.exe"))
            candidates.append(str(Path(env_value) / "platform-tools" / "adb"))

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        sdk_root = Path(local_appdata) / "Android" / "Sdk" / "platform-tools"
        candidates.append(str(sdk_root / "adb.exe"))
        candidates.append(str(sdk_root / "adb"))

    seen = set()
    for candidate in candidates:
        normalized = str(candidate).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate and Path(candidate).exists():
            return str(Path(candidate))

    raise ADBError(
        "Unable to locate adb. Install Android platform-tools or pass adb_path explicitly."
    )


class ADBClient(object):
    """Thin wrapper around adb for device interaction only."""

    ADB_KEYBOARD_IME_ID = "com.android.adbkeyboard/.AdbIME"

    def __init__(
        self,
        adb_path: Optional[str] = None,
        device_id: Optional[str] = None,
        timeout: int = 15,
    ) -> None:
        self.adb_path = find_adb_path(adb_path)
        self.device_id = device_id
        self.timeout = timeout

    def _build_cmd(self, *args: str) -> List[str]:
        cmd = [self.adb_path]
        if self.device_id:
            cmd.extend(["-s", self.device_id])
        cmd.extend(list(args))
        return cmd

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: Optional[int] = None,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        real_timeout = timeout if timeout is not None else self.timeout
        cmd = self._build_cmd(*args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=text,
                encoding="utf-8" if text else None,
                errors="replace" if text else None,
                timeout=real_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ADBError("ADB command timed out: {0}".format(" ".join(cmd))) from exc
        except FileNotFoundError as exc:
            raise ADBError("ADB executable not found: {0}".format(self.adb_path)) from exc

        if check and result.returncode != 0:
            stdout = result.stdout if text else "<binary>"
            stderr = result.stderr if text else "<binary>"
            raise ADBError(
                "ADB command failed\n"
                "command: {0}\n"
                "return_code: {1}\n"
                "stdout:\n{2}\n"
                "stderr:\n{3}".format(" ".join(cmd), result.returncode, stdout, stderr)
            )
        return result

    def shell(self, command: str, check: bool = True, timeout: Optional[int] = None) -> str:
        return self.run("shell", command, check=check, timeout=timeout).stdout.strip()

    def start_server(self) -> None:
        self.run("start-server")

    def list_devices(self, only_ready: bool = False) -> List[Dict[str, str]]:
        output = self.run("devices").stdout.strip().splitlines()
        devices = []
        for line in output[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            item = {"device_id": parts[0], "status": parts[1]}
            if only_ready and item["status"] != "device":
                continue
            devices.append(item)
        return devices

    def ensure_device(self, timeout: int = 30) -> str:
        if self.device_id:
            if self.device_id in [item["device_id"] for item in self.list_devices(True)]:
                return self.device_id
        start = time.time()
        while time.time() - start < timeout:
            ready_devices = self.list_devices(only_ready=True)
            if self.device_id:
                if self.device_id in [item["device_id"] for item in ready_devices]:
                    return self.device_id
                time.sleep(1)
                continue
            if ready_devices:
                self.device_id = ready_devices[0]["device_id"]
                return self.device_id
            time.sleep(1)
        raise ADBError("No ready adb device was found within {0}s.".format(timeout))

    def is_device_connected(self) -> bool:
        try:
            self.ensure_device(timeout=1)
            return True
        except ADBError:
            return False

    def tap(self, x: int, y: int) -> None:
        self.shell("input tap {0} {1}".format(int(x), int(y)))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self.shell(
            "input swipe {0} {1} {2} {3} {4}".format(
                int(x1), int(y1), int(x2), int(y2), int(duration_ms)
            )
        )

    def keyevent(self, key_code: int) -> None:
        self.shell("input keyevent {0}".format(int(key_code)))

    def input_text(self, text: str) -> None:
        escaped = text.replace(" ", "%s")
        for source, target in (
            ("\\", "\\\\"),
            ('"', '\\"'),
            ("&", "\\&"),
            ("|", "\\|"),
            ("<", "\\<"),
            (">", "\\>"),
            ("(", "\\("),
            (")", "\\)"),
            (";", "\\;"),
        ):
            escaped = escaped.replace(source, target)
        escaped = escaped.replace("\n", "%s")
        self.shell('input text "{0}"'.format(escaped))

    def list_input_methods(self) -> List[str]:
        output = self.shell("ime list -s", check=False)
        return [line.strip() for line in output.splitlines() if line.strip()]

    def current_input_method(self) -> str:
        return self.shell("settings get secure default_input_method", check=False).strip()

    def has_input_method(self, ime_id: str) -> bool:
        return ime_id in self.list_input_methods()

    def enable_input_method(self, ime_id: str) -> None:
        self.shell("ime enable {0}".format(ime_id), check=False)

    def set_input_method(self, ime_id: str) -> None:
        self.shell("ime set {0}".format(ime_id))

    def input_text_via_adb_keyboard(self, text: str) -> bool:
        if not self.has_input_method(self.ADB_KEYBOARD_IME_ID):
            return False
        original_ime = self.current_input_method()
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        try:
            self.enable_input_method(self.ADB_KEYBOARD_IME_ID)
            self.set_input_method(self.ADB_KEYBOARD_IME_ID)
            self.shell("am broadcast -a ADB_INPUT_B64 --es msg {0}".format(encoded))
            return True
        finally:
            if original_ime and original_ime != self.ADB_KEYBOARD_IME_ID:
                self.shell("ime set {0}".format(original_ime), check=False)

    def input_text_best_effort(self, text: str) -> str:
        if self.input_text_via_adb_keyboard(text):
            return "adb_keyboard"
        self.input_text(text)
        return "shell_input"

    def back(self) -> None:
        self.keyevent(4)

    def home(self) -> None:
        self.keyevent(3)

    def screenshot(self, save_path: str) -> Path:
        target = Path(save_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        cmd = self._build_cmd("exec-out", "screencap", "-p")
        try:
            with target.open("wb") as handle:
                result = subprocess.run(
                    cmd,
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired as exc:
            raise ADBError("ADB screenshot timed out.") from exc
        if result.returncode != 0:
            raise ADBError(
                "Failed to capture screenshot: {0}".format(
                    result.stderr.decode("utf-8", errors="replace")
                )
            )
        return target

    def open_app(
        self, package_name: str, activity_name: Optional[str] = None, wait_time: float = 1.0
    ) -> None:
        if activity_name:
            component = "{0}/{1}".format(package_name, activity_name)
            self.shell("am start -n {0}".format(component))
        else:
            self.shell(
                "monkey -p {0} -c android.intent.category.LAUNCHER 1".format(package_name)
            )
        if wait_time > 0:
            time.sleep(wait_time)

    def open_url(
        self,
        url: str,
        package_name: Optional[str] = None,
        wait_time: float = 1.0,
    ) -> None:
        safe_url = str(url or "").strip().replace('"', "%22")
        if not safe_url:
            raise ADBError("open_url requires a non-empty url.")
        command = 'am start -a android.intent.action.VIEW -d "{0}"'.format(safe_url)
        if package_name:
            command += ' -p "{0}"'.format(str(package_name).strip())
        self.shell(command)
        if wait_time > 0:
            time.sleep(wait_time)

    def force_stop_app(self, package_name: str) -> None:
        self.shell("am force-stop {0}".format(package_name), check=False)

    def is_package_installed(self, package_name: str) -> bool:
        output = self.shell("pm path {0}".format(package_name), check=False)
        return output.strip().startswith("package:")

    def start_sendto_intent(
        self,
        phone_number: str,
        body: Optional[str] = None,
        wait_time: float = 1.0,
    ) -> None:
        sms_uri = "sms:{0}".format(quote(phone_number, safe="+0123456789"))
        command = 'am start -a android.intent.action.SENDTO -d "{0}"'.format(sms_uri)
        if body is not None:
            escaped_body = body.replace("\\", "\\\\").replace('"', '\\"')
            command += ' --es sms_body "{0}"'.format(escaped_body)
        self.shell(command)
        if wait_time > 0:
            time.sleep(wait_time)

    def start_calendar_event_intent(
        self,
        title: str,
        begin_time_ms: Optional[int] = None,
        package_name: str = "com.google.android.calendar",
        wait_time: float = 1.0,
    ) -> None:
        escaped_title = title.replace("\\", "\\\\").replace('"', '\\"')
        command = (
            'am start -a android.intent.action.INSERT '
            '-t vnd.android.cursor.item/event '
            '-p "{0}" --es title "{1}"'.format(package_name, escaped_title)
        )
        if begin_time_ms is not None:
            command += " --el beginTime {0}".format(int(begin_time_ms))
        self.shell(command)
        if wait_time > 0:
            time.sleep(wait_time)

    def get_screen_size(self) -> Tuple[int, int]:
        output = self.shell("wm size")
        match = re.search(r"(\d+)x(\d+)", output)
        if not match:
            raise ADBError("Unable to parse screen size from: {0}".format(output))
        return int(match.group(1)), int(match.group(2))

    def get_current_focus(self) -> str:
        output = self.shell(
            "dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp'",
            check=False,
        )
        if not output:
            output = self.shell("dumpsys window | findstr mCurrentFocus", check=False)
        return output.strip()

    def dump_ui_xml(self, local_path: str) -> Path:
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        last_error = None
        attempts = (
            ("/sdcard/window_dump.xml", ("--compressed",), 0.6),
            ("/sdcard/window_dump.xml", tuple(), 1.0),
            ("/data/local/tmp/window_dump.xml", ("--compressed",), 1.2),
            ("/data/local/tmp/window_dump.xml", tuple(), 1.5),
        )

        for remote_path, dump_args, settle_delay in attempts:
            self.shell("rm {0}".format(remote_path), check=False)
            time.sleep(settle_delay)
            result = self.run(
                "shell",
                "uiautomator",
                "dump",
                *dump_args,
                remote_path,
                check=False,
                timeout=max(self.timeout, 25),
            )
            if result.returncode == 0:
                remote_check = self.shell("ls -l {0}".format(remote_path), check=False)
                if remote_check and "No such file" not in remote_check:
                    try:
                        self.run("pull", remote_path, str(target), timeout=max(self.timeout, 25))
                        self.shell("rm {0}".format(remote_path), check=False)
                        return target
                    except ADBError as exc:
                        last_error = exc
                else:
                    last_error = ADBError(
                        "UI dump file was not created on device at {0}.".format(remote_path)
                    )
            else:
                last_error = ADBError(
                    "uiautomator dump failed with code {0}. stdout={1} stderr={2}".format(
                        result.returncode,
                        result.stdout.strip(),
                        result.stderr.strip(),
                    )
                )
            time.sleep(1.0)

        raise last_error or ADBError("Failed to dump UI hierarchy.")
