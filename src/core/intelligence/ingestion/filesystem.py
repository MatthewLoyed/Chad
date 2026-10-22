"""
Local Filesystem Catalog Ingestor
Walks user-allowlisted directory roots and indexes filename + path + mtime so Chad can
answer "where's that file?" without full-body content indexing (issue #601, v1 scope).

Privacy: roots are opt-in and empty by default (no scan). Never scans the whole home
directory or system paths on its own -- the user must list specific folders.
"""

import fnmatch
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.core.intelligence.ingestion.base import BaseIngestor
from src.shared.config_manager import ConfigManager

logger = logging.getLogger("FilesystemIngestor")

# Named noise dirs. Beyond these, skip_hidden_dirs below prunes dot-prefixed dirs
# generically (tool caches/config: .lmstudio, .ollama, .cache, .vscode, .docker, ...)
# without needing to enumerate every possible one.
DEFAULT_EXCLUDE_GLOBS = [
    "**/node_modules/**",
    "**/.git/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "**/AppData/**",
    "**/$RECYCLE.BIN/**",
    "**/System Volume Information/**",
    "**/dist/**",
    "**/build/**",
    "**/.next/**",
    "**/.turbo/**",
    "**/target/**",
    "**/.pytest_cache/**",
    "**/.mypy_cache/**",
    "**/.tox/**",
    # Embedded browser (WebView2/CEF) runtime cache dirs that desktop apps drop next to
    # their data -- e.g. chad-hud/data/webview_cache/EBWebView/... Pure runtime junk.
    "**/webview_cache/**",
    "**/EBWebView/**",
    # Unity/dotnet build cache -- Library/Temp regenerate from source and aren't
    # "documents"; obj/ is C# build output.
    "**/Library/**",
    "**/Temp/**",
    "**/obj/**",
    # .NET build output / restored packages -- regenerate from source, never "documents".
    "**/bin/Debug/**",
    "**/bin/Release/**",
    "**/packages/**",
    # Sqlite WAL/journal sidecar files are rewritten continuously by ANY app using
    # them (this one included) -- their mtime changes on basically every write, so
    # cataloging them isn't just noise, it can make a file look "always new".
    "**/*.db-wal",
    "**/*.db-shm",
    "**/*.db-journal",
]

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v"}

# Model-weight formats: large by nature, not "documents" a user searches for by name.
# Caught primarily by the size cutoff below, but flagged by extension too since some
# ship as many small shards that individually dodge the size threshold.
MODEL_WEIGHT_EXTENSIONS = {".safetensors", ".gguf", ".ckpt", ".onnx", ".pt", ".pth"}

# Auto-generated sidecar/noise files with no standalone search value -- e.g. Unity
# writes one .meta per asset (frequently the single biggest source of catalog noise
# in a game-dev tree).
NOISE_EXTENSIONS = {".meta", ".modulecompilationtrigger"}


def is_excluded(path: str, exclude_globs: List[str]) -> bool:
    """True if the normalized path matches any exclude glob pattern."""
    normalized = path.replace("\\", "/")
    for pattern in exclude_globs:
        if fnmatch.fnmatch(normalized, pattern):
            return True
    return False


def is_hidden_dir(dirname: str) -> bool:
    """Dot-prefixed directory name (.git, .venv, .lmstudio, .cache, .vscode, ...)."""
    return dirname.startswith(".")


def is_video(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in VIDEO_EXTENSIONS


def is_model_weight(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in MODEL_WEIGHT_EXTENSIONS


def is_noise_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in NOISE_EXTENSIONS


def own_storage_exclude_globs() -> List[str]:
    """Exclude Chad's own local sqlite store (main file + WAL/SHM/journal sidecars).

    Real bug hit in production (issue #601): if a configured root happens to contain
    Chad's own data/ dir (e.g. the repo lives under Documents/...), every write to
    local_chad.db updates its own mtime, so the catalog perpetually "discovers" it as
    a new file every single sync cycle -- an infinite self-referential loop that never
    converges. Resolved dynamically via local_store.get_local_db_path() so this works
    regardless of where a given user's clone/CHAD_HOME actually lives, not just this
    one path.
    """
    try:
        from src.infrastructure.database import local_store
        db_path = local_store.get_local_db_path().replace("\\", "/")
        return [db_path, db_path + "-wal", db_path + "-shm", db_path + "-journal"]
    except Exception:
        return []


def format_catalog_entry(name: str, path: str, mtime_iso: str) -> str:
    """Dense searchable payload for a catalogued file."""
    return f"File: '{name}' at {path}\nLast Modified: {mtime_iso}"


class FilesystemCatalogIngestor(BaseIngestor):
    """
    Catalogs files under user-configured allowlisted roots: name, path, mtime, type.
    No file content is read in v1 -- catalog only.
    """

    def get_source_name(self) -> str:
        return "filesystem"

    def _get_config(self) -> Dict[str, Any]:
        return {
            "enabled": ConfigManager.get("filesystem_catalog.enabled", False),
            "roots": ConfigManager.get("filesystem_catalog.roots", []) or [],
            "exclude_globs": ConfigManager.get(
                "filesystem_catalog.exclude_globs", DEFAULT_EXCLUDE_GLOBS
            ),
            "skip_videos": ConfigManager.get("filesystem_catalog.skip_videos", True),
            "skip_hidden_dirs": ConfigManager.get("filesystem_catalog.skip_hidden_dirs", True),
            "skip_model_weights": ConfigManager.get("filesystem_catalog.skip_model_weights", True),
            # Files larger than this are almost never "that document I'm looking for" --
            # model checkpoints, VM images, ISOs, disk backups. Metadata-only catalog
            # doesn't care about size for cost, but size is a strong noise signal.
            "max_file_size_mb": ConfigManager.get("filesystem_catalog.max_file_size_mb", 1024),
            "max_files": ConfigManager.get("filesystem_catalog.max_files", 20000),
        }

    def ingest(self, last_sync: Optional[datetime] = None) -> List[Dict[str, Any]]:
        config = self._get_config()

        if not config["enabled"] or not config["roots"]:
            logger.debug(
                "[ FilesystemIngestor ] Disabled or no roots configured -- skipping scan."
            )
            return []

        exclude_globs = list(config["exclude_globs"]) + own_storage_exclude_globs()
        skip_videos = config["skip_videos"]
        skip_hidden_dirs = config["skip_hidden_dirs"]
        skip_model_weights = config["skip_model_weights"]
        max_file_size_bytes = config["max_file_size_mb"] * 1024 * 1024
        max_files = config["max_files"]
        # Hard ceiling on how many candidates we'll hold in memory before selecting the
        # oldest max_files of them. Kept well above max_files so a directory-walk-order
        # truncation can't strand older files in an unwalked directory behind a watermark
        # advanced past them (that would silently drop them from every future sync).
        scan_ceiling = max(max_files * 5, 50000)

        scanned = []
        for root in config["roots"]:
            expanded_root = os.path.expandvars(os.path.expanduser(root))
            if not os.path.isdir(expanded_root):
                logger.warning(
                    f"[ FilesystemIngestor ] Configured root does not exist: {expanded_root}"
                )
                continue

            for dirpath, dirnames, filenames in os.walk(expanded_root):
                if is_excluded(dirpath, exclude_globs):
                    dirnames[:] = []
                    continue

                if skip_hidden_dirs:
                    dirnames[:] = [d for d in dirnames if not is_hidden_dir(d)]

                for filename in filenames:
                    if len(scanned) >= scan_ceiling:
                        break

                    full_path = os.path.join(dirpath, filename)

                    if is_excluded(full_path, exclude_globs):
                        continue
                    if skip_videos and is_video(full_path):
                        continue
                    if skip_model_weights and is_model_weight(full_path):
                        continue
                    if is_noise_file(full_path):
                        continue

                    try:
                        stat = os.stat(full_path)
                    except OSError:
                        continue

                    if stat.st_size > max_file_size_bytes:
                        continue

                    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                    if last_sync and mtime <= last_sync:
                        continue

                    scanned.append((full_path, filename, mtime, stat.st_size))

                if len(scanned) >= scan_ceiling:
                    logger.warning(
                        f"[ FilesystemIngestor ] Hit scan ceiling ({scan_ceiling}) -- "
                        "some directories may not have been visited this cycle."
                    )
                    break
            if len(scanned) >= scan_ceiling:
                break

        # Sort oldest-to-newest so incremental watermarking works like other ingestors,
        # and so truncating to max_files below always keeps the OLDEST candidates -- the
        # ones that must be processed before the watermark can safely advance past them.
        scanned.sort(key=lambda item: item[2])
        if len(scanned) > max_files:
            logger.warning(
                f"[ FilesystemIngestor ] {len(scanned)} candidate files exceed max_files "
                f"({max_files}) -- processing the oldest {max_files}; the rest will be "
                "picked up on subsequent sync cycles."
            )
            scanned = scanned[:max_files]

        normalized_items = []
        for full_path, filename, mtime, size in scanned:
            mtime_iso = mtime.isoformat()
            content_payload = format_catalog_entry(filename, full_path, mtime_iso)

            dedup_key = f"{full_path}:{mtime_iso}"
            item_hash = self.compute_hash(dedup_key)

            _, ext = os.path.splitext(filename)

            normalized_items.append({
                "content": content_payload,
                "importance": 0.4,
                "metadata": {
                    "source": "filesystem",
                    "path": full_path,
                    "name": filename,
                    "extension": ext.lower(),
                    "size_bytes": size,
                    "timestamp": mtime_iso,
                    "hash": item_hash,
                },
            })

        logger.info(
            f"[ FilesystemIngestor ] Catalogued {len(normalized_items)} file(s) across "
            f"{len(config['roots'])} root(s)."
        )
        return normalized_items
