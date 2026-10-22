"""Compare CPU SentenceTransformer vs LM Studio embeddings throughput.

Usage (repo root, LM Studio embed model loaded):
  set PYTHONPATH=.
  python scripts/maintenance/bench_embeddings.py
"""

from __future__ import annotations

import time
from typing import List


TEXTS_SMALL = [
    "Google Drive file 'Matthew & Noel's Wedding Roadmap' at My Drive/Wedding (document)",
    "Email from Layne, Bryan about compensation and pay",
    "Watched YouTube Video: Streamer University day one moments",
]

TEXTS_MED = [
    f"Catalog row {i}: Golfport financial document phase {i % 7} revenue projection notes"
    for i in range(64)
]


def _reset():
    import src.core.intelligence.local_embeddings as le
    import src.core.intelligence.embedding_provider as ep

    le.reset_embedding_model_singleton()
    ep._local_embedding_model = None


def _bench(label: str, texts: List[str], *, use_api: bool, batch: bool) -> None:
    from src.shared.config_manager import ConfigManager
    from src.core.intelligence.local_embeddings import get_embedding_model

    _reset()
    ConfigManager.load_config()
    # Force path for this trial
    ConfigManager._config = ConfigManager._config or {}
    ConfigManager._config.setdefault("embeddings", {})
    ConfigManager._config["embeddings"]["use_api"] = use_api
    ConfigManager._config["embeddings"]["api_model"] = "text-embedding-nomic-embed-text-v2-moe"
    ConfigManager._config["embeddings"]["batch_size"] = 64
    ConfigManager._config["embeddings"]["base_url"] = "http://localhost:1234/v1"

    model = get_embedding_model("nomic-ai/nomic-embed-text-v2-moe")
    # Warmup
    _ = model.encode(texts[0])

    t0 = time.perf_counter()
    if batch:
        embs = model.encode(texts)
        n = len(embs)
    else:
        embs = [model.encode(t) for t in texts]
        n = len(embs)
    elapsed = time.perf_counter() - t0
    dim = len(embs[0]) if embs else 0
    rate = n / elapsed if elapsed > 0 else 0
    print(
        f"{label:40s} n={n:4d} dim={dim}  {elapsed:6.2f}s  {rate:7.1f}/s  "
        f"api={use_api} batch={batch}"
    )


def main() -> int:
    from src.shared.config_manager import ConfigManager

    ConfigManager.load_config()
    print("LM Studio probe + CPU ST vs GPU API (AMD via LM Studio)\n")

    # 1) Couple items — single vs batch on API
    _bench("API single (3)", TEXTS_SMALL, use_api=True, batch=False)
    _bench("API batch  (3)", TEXTS_SMALL, use_api=True, batch=True)

    # 2) Couple items — CPU ST (no API)
    _bench("CPU ST single (3)", TEXTS_SMALL, use_api=False, batch=False)
    _bench("CPU ST batch  (3)", TEXTS_SMALL, use_api=False, batch=True)

    # 3) Larger — API batch vs CPU batch
    _bench("API batch  (64)", TEXTS_MED, use_api=True, batch=True)
    _bench("CPU ST batch (64)", TEXTS_MED, use_api=False, batch=True)

    # 4) API single 64 (worst case ingest before batching)
    _bench("API single (64)", TEXTS_MED, use_api=True, batch=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
