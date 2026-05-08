from __future__ import annotations

from pathlib import Path

from app.utils.adb import ADBClient, ADBError
from app.utils.device_actions import DeviceActions


def main() -> None:
    try:
        adb = ADBClient()
        actions = DeviceActions(adb)

        actions.ensure_device()
        print("ADB path:", adb.adb_path)
        print("Devices:", adb.list_devices())
        print("Current device:", adb.device_id)
        print("Screen size:", actions.screen_size())
        print("Current focus:", actions.current_focus())

        print("\n1. Return home")
        actions.home()
        actions.sleep(1)

        print("2. Open Chrome")
        actions.open_chrome()
        actions.sleep(2)

        print("3. Capture screenshot")
        save_path = actions.screenshot(str(Path("data/tmp/manual_screen1.png")))
        print("Saved screenshot:", save_path)
    except ADBError as exc:
        print("ADBError:", exc)
    except Exception as exc:  # pragma: no cover - manual smoke test
        print("OtherError:", exc)


if __name__ == "__main__":
    main()
