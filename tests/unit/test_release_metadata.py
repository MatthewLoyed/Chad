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
