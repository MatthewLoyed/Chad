"""
Audio Monitor - Windows Core Audio API (Push Architecture)
Uses COM/IMMNotificationClient to detect default device changes instantly.
"""

import threading
import time
import logging
import pythoncom
import comtypes
from comtypes import COMObject, CLSCTX_ALL
from ctypes import POINTER, cast

# ========================================================================================
# MANUALLY DEFINED COM INTERFACES (Eliminates dependency on unstable type-generation)
# ========================================================================================

from comtypes import GUID, HRESULT, COMMETHOD, IUnknown
from ctypes import c_wchar_p, c_void_p, c_uint

class EMP (object):
    eRender = 0
    eCapture = 1
    eAll = 2

class ERole (object):
    eConsole = 0
    eMultimedia = 1
    eCommunications = 2

class IMMNotificationClient(IUnknown):
    _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
    _methods_ = [
        COMMETHOD([], HRESULT, "OnDeviceStateChanged",
                  (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_uint, "dwNewState")),
        COMMETHOD([], HRESULT, "OnDeviceAdded",
                  (["in"], c_wchar_p, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDeviceRemoved",
                  (["in"], c_wchar_p, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDefaultDeviceChanged",
                  (["in"], c_uint, "flow"),
                  (["in"], c_uint, "role"),
                  (["in"], c_wchar_p, "pwstrDefaultDeviceId")),
        COMMETHOD([], HRESULT, "OnPropertyValueChanged",
                  (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_void_p, "key")), # simplified
    ]

# ========================================================================================
# MANUALLY DEFINED COM INTERFACES (Eliminates dependency on unstable type-generation)
# ========================================================================================

from comtypes import GUID, HRESULT, COMMETHOD, IUnknown
from ctypes import c_wchar_p, c_void_p, c_uint

class IMMNotificationClient(IUnknown):
    _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
    _methods_ = [
        COMMETHOD([], HRESULT, "OnDeviceStateChanged",
                  (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_uint, "dwNewState")),
        COMMETHOD([], HRESULT, "OnDeviceAdded",
                  (["in"], c_wchar_p, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDeviceRemoved",
                  (["in"], c_wchar_p, "pwstrDeviceId")),
        COMMETHOD([], HRESULT, "OnDefaultDeviceChanged",
                  (["in"], c_uint, "flow"),
                  (["in"], c_uint, "role"),
                  (["in"], c_wchar_p, "pwstrDefaultDeviceId")),
        COMMETHOD([], HRESULT, "OnPropertyValueChanged",
                  (["in"], c_wchar_p, "pwstrDeviceId"),
                  (["in"], c_void_p, "key")), 
    ]

class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_uint, "dataFlow"),
                  (["in"], c_uint, "dwStateMask"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_uint, "dataFlow"),
                  (["in"], c_uint, "role"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppEndpoint")),
        COMMETHOD([], HRESULT, "GetDevice",
                  (["in"], c_wchar_p, "pwstrId"),
                  (["out"], POINTER(POINTER(IUnknown)), "ppDevice")),
        COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
                  (["in"], POINTER(IMMNotificationClient), "pClient")),
        COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
                  (["in"], POINTER(IMMNotificationClient), "pClient")),
    ]

class MMDeviceEnumerator(object):
    # CLSID_MMDeviceEnumerator
    _reg_clsid_ = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    _com_interfaces_ = [IMMDeviceEnumerator]

# Compatibility alias for existing code references

# Note: MMDeviceAPI alias removed to prevent scope confusion. Use classes directly.

from src.shared.logger import setup_logger

logger = setup_logger("AudioMonitor")
logger.setLevel(logging.WARNING)  # Reduce verbosity - only show warnings/errors

class AudioMonitor(COMObject):
    """
    COM Object implementing IMMNotificationClient.
    Must be instantiated within the thread that runs the message pump.
    """
    _com_interfaces_ = [IMMNotificationClient]

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self._last_change_time = 0
        self._debounce_window = 0.5 # 500ms debounce
        self._last_device_id = None

    def OnDefaultDeviceChanged(self, flow, role, pwstrDefaultDeviceId):
        """
        Called when the user (or OS) changes the default audio device.
        flow: 0 = eRender (Playback), 1 = eCapture (Recording)
        role: 0 = eConsole (Standard), 1 = eMultimedia, 2 = eCommunications
        """
        # Windows often fires 3 events (Console, Multimedia, Comm) in rapid succession
        # We only care about Console (role 0) to avoid triple-triggering
        if role != 0:
            return
            
        current_time = time.time()
        # Debounce
        if (current_time - self._last_change_time < self._debounce_window) and (pwstrDefaultDeviceId == self._last_device_id):
            return
        
        self._last_change_time = current_time
        self._last_device_id = pwstrDefaultDeviceId
        
        if flow == 0:
            # Output device changed
            logger.info(f"[ AUDIO MONITOR ] : Default OUTPUT Device Changed -> {pwstrDefaultDeviceId}")
            self.callback("switch_output", pwstrDefaultDeviceId)
        elif flow == 1:
            # Input device changed
            logger.info(f"[ AUDIO MONITOR ] : Default INPUT Device Changed -> {pwstrDefaultDeviceId}")
            self.callback("switch_input", pwstrDefaultDeviceId)

    def OnDeviceAdded(self, pwstrDeviceId):
        """Called when a new device is added (e.g. plugged in)."""
        logger.info(f"[ AUDIO MONITOR ] : Device Added -> {pwstrDeviceId}")
        self.callback("added", pwstrDeviceId)

    def OnDeviceRemoved(self, pwstrDeviceId):
        """Called when a device is removed (e.g. unplugged)."""
        logger.info(f"[ AUDIO MONITOR ] : Device Removed -> {pwstrDeviceId}")
        self.callback("removed", pwstrDeviceId)

    def OnDeviceStateChanged(self, pwstrDeviceId, dwNewState):
        """Called when device state changes (Active, Disabled, Unplugged, NotPresent)."""
        # DEVICE_STATE_ACTIVE = 1
        if dwNewState == 1:
            logger.debug(f"[ AUDIO MONITOR ] : Device Active -> {pwstrDeviceId}")
            self.callback("active", pwstrDeviceId)

    def OnPropertyValueChanged(self, pwstrDeviceId, key):
        pass


class AudioDeviceListener:
    """
    Manages the background thread and COM message pump for the AudioMonitor.
    """
    def __init__(self, callback):
        self.callback = callback
        self._thread = None
        self._running = False
        self._monitor = None
        self._enumerator = None
        
    def start(self):
        """Start the monitoring thread."""
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._run_monitor, daemon=True, name="AudioMonitorThread")
        self._thread.start()
        logger.info("[ AUDIO MONITOR ] : Listener thread started.")

    def stop(self):
        """Stop the monitoring thread."""
        self._running = False
        # Note: Thread is daemon, so we mostly rely on main app exit, 
        # but pure COM cleanup is tricky from outside the thread.
        
    def _run_monitor(self):
        """Background thread main loop."""
        try:
            # 1. Initialize COM in this thread (Critical!)
            pythoncom.CoInitialize()
            
            # 2. Create the Enumerator
            # The CLSID must be a GUID instance, not a Python class type.
            CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
            enumerator = comtypes.CoCreateInstance(
                CLSID_MMDeviceEnumerator,
                IMMDeviceEnumerator,
                CLSCTX_ALL
            )
            
            # 3. Create our Callback Object
            self._monitor = AudioMonitor(self.callback)
            
            # 4. Register the Endpoint Notification Callback
            # Ensure we are passing the pointer interface
            enumerator.RegisterEndpointNotificationCallback(self._monitor)

            self._enumerator = enumerator # Keep reference to prevent GC
            
            logger.info("[ AUDIO MONITOR ] : Notification Callback Registered. Listening for events...")
            
            # 5. Message Pump (Required for COM callbacks to fire)
            while self._running:
                # PumpMessages processes all pending messages for this thread
                # It blocks until a message is received or timeout (if implemented manually)
                # But pythoncom.PumpMessages() is blocking. 
                # We use PumpWaitingMessages to allow checking self._running
                pythoncom.PumpWaitingMessages()
                time.sleep(0.05) # 20Hz poll for messages is sufficient for this
                
        except Exception as e:
            logger.error(f"[ AUDIO MONITOR ] : Listener Crashed: {e}")
        finally:
            try:
                if self._enumerator and self._monitor:
                    self._enumerator.UnregisterEndpointNotificationCallback(self._monitor)
            except:
                pass
            pythoncom.CoUninitialize()
            logger.info("[ AUDIO MONITOR ] : Listener thread stopped.")
