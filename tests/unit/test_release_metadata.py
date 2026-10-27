import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("release_metadata", Path(__file__).resolve().parents[2] / "scripts/prod/release_metadata.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_next_alpha_uses_numeric_order_and_reserves_existing_drafts():
    assert release.next_version([{"tag_name": "v0.2.0-alpha.9"}, {"tag_name": "v0.2.0-alpha.10", "draft": True}]) == "0.2.0-alpha.11"


@pytest.mark.parametrize("draft", [True, False])
def test_rebuild_cannot_replace_an_existing_draft_or_published_release(draft):
    releases = [{"tag_name": "v0.2.0-alpha.13", "draft": draft}]
    with pytest.raises(ValueError, match="already exists"):
        release.ensure_new_release("0.2.0-alpha.13", releases)
    assert release.ensure_new_release("0.2.0-alpha.14", releases) == "0.2.0-alpha.14"


def test_release_inventory_includes_all_pages(monkeypatch):
    monkeypatch.setattr(release.subprocess, "check_output", lambda *args, **kwargs: json.dumps([
        [{"tag_name": "v0.2.0-alpha.14", "draft": True}],
        [{"tag_name": "v0.2.0-alpha.13", "draft": False}],
    ]))
    with pytest.raises(ValueError, match="already exists"):
        release.ensure_new_release("0.2.0-alpha.13", release.repository_releases())


def test_provenance_matches_installer_bytes(tmp_path):
    installer = tmp_path / "Chad-Setup-0.2.0-alpha.11.exe"
    installer.write_bytes(b"fictional installer fixture")
    manifest = json.loads(release.write_provenance(installer, "0.2.0-alpha.11", "a" * 40).read_text())
    assert manifest["sha256"] == hashlib.sha256(installer.read_bytes()).hexdigest()
    assert manifest["bytes"] == installer.stat().st_size
    assert (tmp_path / "SHA256SUMS").read_text().endswith(installer.name + "\n")


def test_download_update_changes_only_the_current_release_block(tmp_path):
    readme = tmp_path / "README.md"
    original = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    readme.write_text(original + "\nHistorical v0.2.0-alpha.9\n")
    release.update_download(readme, "v0.2.0-alpha.11")
    text = readme.read_text()
    assert "Chad-Setup-0.2.0-alpha.11.exe" in text
    assert "/releases/tag/v0.2.0-alpha.11" in text
    assert "Historical v0.2.0-alpha.9" in text
    with pytest.raises(ValueError):
        release.update_download(readme, "../invalid")
