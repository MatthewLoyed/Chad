"""
Audio Device Probe (SoundDevice version)
Lists all available audio input/output devices for selection.
"""

import sounddevice as sd
from typing import List, Dict, Any


def list_audio_devices() -> Dict[str, List[Dict[str, Any]]]:
    """
    List all available audio devices using sounddevice.
    
    Returns:
        Dictionary with 'input' and 'output' device lists
    """
    input_devices = []
    output_devices = []
    
    try:
        devices = sd.query_devices()
        host_apis = sd.query_hostapis()
        
        for i, info in enumerate(devices):
            try:
                device_info = {
                    'index': i,
                    'name': info.get('name', 'Unknown'),
                    'channels': {
                        'input': info.get('max_input_channels', 0),
                        'output': info.get('max_output_channels', 0)
                    },
                    'sample_rate': int(info.get('default_samplerate', 0)),
                    'host_api': host_apis[info.get('hostapi', 0)].get('name', 'Unknown')
                }
                
                # Add to input devices if it has input channels
                if device_info['channels']['input'] > 0:
                    input_devices.append(device_info)
                
                # Add to output devices if it has output channels
                if device_info['channels']['output'] > 0:
                    output_devices.append(device_info)
                    
            except Exception:
                # Skip devices that can't be queried
                continue
    except Exception:
        pass
    
    return {
        'input': input_devices,
        'output': output_devices
    }


def print_audio_devices():
    """Print all available audio devices to terminal."""
    devices = list_audio_devices()
    
    print("\n" + "=" * 80)
    print("AUDIO INPUT DEVICES")
    print("=" * 80)
    for device in devices['input']:
        print(f"  [{device['index']}] {device['name']}")
        print(f"      Channels: {device['channels']['input']}, Sample Rate: {device['sample_rate']}Hz")
        print(f"      Host API: {device['host_api']}")
        print()
    
    print("\n" + "=" * 80)
    print("AUDIO OUTPUT DEVICES")
    print("=" * 80)
    for device in devices['output']:
        print(f"  [{device['index']}] {device['name']}")
        print(f"      Channels: {device['channels']['output']}, Sample Rate: {device['sample_rate']}Hz")
        print(f"      Host API: {device['host_api']}")
        print()
    
    print("=" * 80)
    print("\nTo use a specific device, set 'input_device_index' or 'output_device_index' in config.yaml")
    print("Example: input_device_index: 2")


if __name__ == "__main__":
    print_audio_devices()
