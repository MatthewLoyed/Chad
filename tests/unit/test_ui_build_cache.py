"""Regression tests for WebView2 cache invalidation across a one-click update.

The bug: `rebuild_web_client` stamped `ui_build_version.json` with the freshly built
sha/bundle, and `webview_cache_is_stale` keyed off that same stamp — so the post-update
GUI relaunch always thought its cache already matched the new bundle and skipped the
clear, leaving the old UI rendering. The fix separates the build stamp from a distinct
"loaded" marker that only advances when the WebView actually (re)loads.

Run: PYTHONPATH=. python -m pytest tests/unit/test_ui_build_cache.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.shared import ui_build


def _write_dist(root: Path, bundle: str) -> None:
    """Create a minimal valid dist/ whose index.html references `bundle`."""
    assets = root / "src" / "clients" / "web" / "dist" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / bundle).write_text("// js", encoding="utf-8")
    index = root / "src" / "clients" / "web" / "dist" / "index.html"
    index.write_text(f'<script type="module" src="./assets/{bundle}"></script>', encoding="utf-8")


def _rebuild(root: Path, bundle: str) -> None:
    """Mimic rebuild_web_client: new bundle on disk + build-version stamp."""
    _write_dist(root, bundle)
    ui_build.record_ui_build_version(sha="newsha", root=root)


def test_first_launch_clears_then_initialises_marker(tmp_path: Path) -> None:
    _write_dist(tmp_path, "index-aaaa.js")
    # No loaded marker yet → treat as stale so a fresh prod box / manual pull is safe.
    assert ui_build.webview_cache_is_stale(tmp_path) is True
    ui_build.invalidate_stale_webview_cache(tmp_path)
    # Marker now reflects what we loaded → no longer stale.
    assert ui_build.get_recorded_loaded_bundle(tmp_path) == "index-aaaa.js"
    assert ui_build.webview_cache_is_stale(tmp_path) is False


def test_update_then_relaunch_detects_stale_cache(tmp_path: Path) -> None:
    # Initial load.
    _write_dist(tmp_path, "index-aaaa.js")
    ui_build.invalidate_stale_webview_cache(tmp_path)
    assert ui_build.webview_cache_is_stale(tmp_path) is False

    # Brain pulls + rebuilds: new hashed bundle AND a fresh build-version stamp.
    _rebuild(tmp_path, "index-bbbb.js")

    # The regression: despite the build stamp being fresh, the relaunching GUI must
    # still see a stale cache because the LOADED marker is unchanged.
    assert ui_build.webview_cache_is_stale(tmp_path) is True

    # Relaunch clears + advances the loaded marker.
    assert ui_build.invalidate_stale_webview_cache(tmp_path) is True
    assert ui_build.get_recorded_loaded_bundle(tmp_path) == "index-bbbb.js"
    assert ui_build.webview_cache_is_stale(tmp_path) is False


def test_unchanged_relaunch_is_not_stale(tmp_path: Path) -> None:
    _write_dist(tmp_path, "index-aaaa.js")
    ui_build.invalidate_stale_webview_cache(tmp_path)
    # A plain restart with no rebuild must not re-clear the cache (slow startup).
    assert ui_build.invalidate_stale_webview_cache(tmp_path) is False


def test_summarize_build_failure_extracts_tsc_errors() -> None:
    out = ""
    err = (
        "src/store.ts(508,55): error TS2551: Property 'pendingToolApproval' does not exist.\n"
        "src/testkit/legacyFrameBridge.ts(155,38): error TS2339: Property 'awaiting_user' does not exist.\n"
    )
    summary = ui_build._summarize_build_failure(out, err)
    assert "pendingToolApproval" in summary
    assert "awaiting_user" in summary
    assert "(+" not in summary  # only two errors


def test_failed_rebuild_restores_previous_dist(tmp_path: Path, monkeypatch) -> None:
    """A build that empties dist/ then fails must roll back to the last-good bundle.

    Regression: `vite build` clears dist/ before producing output, so a mid-build failure
    (missing deps, tsc error) left prod serving a skeleton-only UI. rebuild_web_client now
    snapshots the valid dist first and restores it on any failure.
    """
    _write_dist(tmp_path, "index-good.js")
    assert ui_build.dist_is_valid(tmp_path) is True

    def _fake_build(web_dir, timeout=600):
        # Mimic vite emptyOutDir wiping dist, then crashing before emitting a bundle.
        import shutil
        dist = ui_build.web_dist_dir(tmp_path)
        shutil.rmtree(dist, ignore_errors=True)
        dist.mkdir(parents=True, exist_ok=True)
        return 1, "", "tsc: command not found"

    monkeypatch.setattr(ui_build, "_run_npm_build", _fake_build)

    ok, detail = asyncio.run(ui_build.rebuild_web_client(tmp_path))
    assert ok is False
    assert "restored previous UI" in detail
    # The previous good bundle is back and serving — not a skeleton.
    assert ui_build.dist_is_valid(tmp_path) is True
    assert ui_build.get_dist_main_bundle(tmp_path) == "index-good.js"
    assert not (ui_build.web_dist_dir(tmp_path).parent / "dist.bak").exists()


def test_rebuild_rejects_zero_exit_with_invalid_dist(tmp_path: Path, monkeypatch) -> None:
    """A build that exits 0 but leaves an invalid dist is treated as failure + rolled back."""
    _write_dist(tmp_path, "index-good.js")

    def _fake_build(web_dir, timeout=600):
        import shutil
        dist = ui_build.web_dist_dir(tmp_path)
        shutil.rmtree(dist, ignore_errors=True)
        dist.mkdir(parents=True, exist_ok=True)  # exit 0 but no index.html / bundle
        return 0, "built", ""

    monkeypatch.setattr(ui_build, "_run_npm_build", _fake_build)

    ok, detail = asyncio.run(ui_build.rebuild_web_client(tmp_path))
    assert ok is False
    assert "INVALID dist" in detail
    assert ui_build.dist_is_valid(tmp_path) is True
    assert ui_build.get_dist_main_bundle(tmp_path) == "index-good.js"


def test_build_stamp_independent_of_loaded_marker(tmp_path: Path) -> None:
    # Build stamp drives needs_web_rebuild; loaded marker drives cache staleness.
    # They must not be the same file, or the original bug returns.
    _write_dist(tmp_path, "index-aaaa.js")
    ui_build.record_ui_build_version(sha="s1", root=tmp_path)
    ui_build.invalidate_stale_webview_cache(tmp_path)
    assert ui_build._version_file_path(tmp_path) != ui_build._loaded_file_path(tmp_path)
    assert ui_build._loaded_file_path(tmp_path).is_file()
