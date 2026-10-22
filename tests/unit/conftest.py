import sys

import pytest

from src.shared.config_manager import ConfigManager


@pytest.fixture(autouse=True)
def _reset_config_manager_state():
    """Reset the ConfigManager singleton before and after every unit test.

    ConfigManager holds its config as mutable class attrs (_config, _prefs,
    _profiles, _loaded), so any test that loads a custom/tmp-path config (or
    mocks .get()) without tearing down leaks that state into whichever test
    runs next — which test that is depends on collection order, so the same
    leak can pass locally and fail in CI (or vice versa) purely because
    Windows-only files are skipped there, shifting execution order (#216).
    Forcing a fresh real load_config() here for every test removes that
    order-dependency at the root instead of chasing individual leak sources.
    """
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False
    yield
    ConfigManager._config = {}
    ConfigManager._prefs = {}
    ConfigManager._profiles = {}
    ConfigManager._loaded = False


# Chad is a Windows desktop app — several packages are declared Windows-only
# in requirements.txt (`; sys_platform == 'win32'`): pywebview, sounddevice,
# pycaw, comtypes, etc. Two test files transitively import those
# at module load time (test_app_controller.py -> app_controller.py -> webview;
# test_security_hardening.py -> src.remote.server -> remote_factory ->
# audio_handler -> sounddevice), so they can only be collected on Windows.
# Skip them on CI's ubuntu-latest runner instead of failing collection for
# the whole suite (#216).
if sys.platform != "win32":
    collect_ignore_glob = [
        "test_app_controller.py",
        "test_security_hardening.py",
    ]
