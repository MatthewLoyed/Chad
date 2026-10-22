import sys
import os
import time
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import sounddevice as sd
    import numpy as np
    HAS_SOUNDDEVICE = True
except ImportError:
    HAS_SOUNDDEVICE = False

try:
    import pyaudio
    import audioop
    HAS_PYAUDIO = True
except ImportError:
    HAS_PYAUDIO = False

def print_header(text):
    print(f"\n{'='*60}\n{text}\n{'='*60}")

def test_pyaudio():
    if not HAS_PYAUDIO:
        print("❌ PyAudio not installed. skip.")
        return

    p = pyaudio.PyAudio()
    print_header("PyAudio Diagnostic")
    print(f"Detected {p.get_device_count()} Audio Devices")
    
    input_devices = []
    for i in range(p.get_device_count()):
        try:
            info = p.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"ID {i}: {info['name']}")
                input_devices.append(i)
        except:
            pass

    if not input_devices:
        print("No input devices found.")
        p.terminate()
        return

    selection = input(f"\nEnter Device ID to test (Candidates: {input_devices}, Default {input_devices[0]}): ")
    device_id = int(selection) if selection.strip() else input_devices[0]
    
    print(f"\nTesting Device ID: {device_id} (PyAudio)")
    print("Speak into your mic! (Press Ctrl+C to stop)")
    
    try:
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True,
                        input_device_index=device_id, frames_per_buffer=1024)
        while True:
            data = stream.read(1024, exception_on_overflow=False)
            rms = audioop.rms(data, 2)
            bars = "█" * int(rms / 100) 
            print(f"\rVolume: {rms:5d} |{bars:<50}|", end="")
    except KeyboardInterrupt:
        print("\nTest Stopped.")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

async def test_sounddevice_scan():
    if not HAS_SOUNDDEVICE:
        print("❌ SoundDevice/NumPy not installed. skip.")
        return

    print_header("SoundDevice Auto-Scan")
    candidates = []
    devices = sd.query_devices()
    for i, info in enumerate(devices):
        if info['max_input_channels'] > 0:
            candidates.append((i, info['name']))
            
    print(f"Found {len(candidates)} input candidates.")
    print("Please speak continuously while we scan...")
    
    for device_id, name in candidates:
        print(f"\n>>> TESTING ID {device_id}: {name}")
        audio_data = []
        def callback(indata, frames, time, status):
            audio_data.append(indata.copy())

        try:
            with sd.InputStream(device=device_id, channels=1, samplerate=16000, callback=callback):
                start_time = time.time()
                max_vol = 0
                while time.time() - start_time < 2:
                    if audio_data:
                        chunk = audio_data.pop(0)
                        rms = np.sqrt(np.mean(chunk**2)) * 32768
                        max_vol = max(max_vol, rms)
                        print(f"\rVol: {rms:5.0f} |{'I'*int(rms/100):<50}|", end="")
                    await asyncio.sleep(0.05)
            
            if max_vol > 500:
                print(f"\n✅ [SUCCESS] Device {device_id} heard you! (Max Vol: {max_vol:.0f})")
                return device_id
        except Exception as e:
            print(f"\n❌ [ERROR] {e}")

async def main():
    print_header("CHAD AUDIO DIAGNOSTIC TOOL")
    print("1. Interactive PyAudio Test (Real-time bar)")
    print("2. Automatic SoundDevice Scan (Finds your mic)")
    print("3. Exit")
    
    choice = input("\nChoice: ")
    if choice == '1':
        test_pyaudio()
    elif choice == '2':
        await test_sounddevice_scan()
    else:
        print("Exiting.")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
