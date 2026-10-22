"""Quick smoke test for AudioSettingsProbe on real hardware."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.clients.desktop.audio_settings_probe import AudioSettingsProbe


async def main():
    probe = AudioSettingsProbe()
    devices = probe.list_input_devices()
    print(f"Found {len(devices)} WASAPI input devices:")
    for d in devices:
        print(f"  [{d['id']}] {d['name']} active={d['active']}")

    if not devices:
        print("FAIL: no devices")
        return 1

    r = await probe.test_microphone(duration=1.5)
    print("Mic test:", r)
    print("OK" if r.get("success") else "FAIL")
    return 0 if r.get("success") else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
