"""ChadBrowser profile pinning — never attach Playwright to real Chrome."""
from src.shared.chad_browser_profile import (
    enforce_playwright_command,
    extract_user_data_dir,
    is_forbidden_browser_profile,
    resolve_chad_user_data_dir,
)


def test_resolve_chad_user_data_dir_is_chadbrowser():
    path = resolve_chad_user_data_dir()
    assert "ChadBrowser" in path
    assert "UserData" in path
    assert "Google" not in path or "ChadBrowser" in path


def test_real_chrome_profile_is_forbidden():
    assert is_forbidden_browser_profile(
        r"C:\Users\x\AppData\Local\Google\Chrome\User Data"
    )
    assert is_forbidden_browser_profile(
        r"C:\Users\x\AppData\Local\Google\Chrome\User Data\Default"
    )
    assert not is_forbidden_browser_profile(resolve_chad_user_data_dir())


def test_enforce_rewrites_real_chrome_path():
    bad = [
        "npx",
        "-y",
        "@playwright/mcp@latest",
        "--browser",
        "chrome",
        "--user-data-dir",
        r"C:\Users\x\AppData\Local\Google\Chrome\User Data\Default",
    ]
    fixed = enforce_playwright_command(bad)
    udd = extract_user_data_dir(fixed)
    assert udd == resolve_chad_user_data_dir()
    assert not is_forbidden_browser_profile(udd)


def test_enforce_injects_missing_user_data_dir():
    cmd = ["npx", "-y", "@playwright/mcp@latest", "--browser", "chrome"]
    fixed = enforce_playwright_command(cmd)
    assert extract_user_data_dir(fixed) == resolve_chad_user_data_dir()


def test_enforce_ignores_non_playwright_servers():
    cmd = ["npx", "-y", "other", "--user-data-dir", r"C:\somewhere"]
    assert enforce_playwright_command(cmd, server_name="fetch") == cmd
