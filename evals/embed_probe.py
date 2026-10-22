"""embed_probe.py — isolate the tool-RAG embedding cost that makes ToolRegistryInjector ~145s.

The pre-9B tool selection spends ~126s in SemanticToolSelector.retrieve_shortlist, which (since
Supabase is disabled -> local RAG) embeds each of ~7 sub-intent queries with the local
nomic-embed-text-v2-moe SentenceTransformer, ALL AT ONCE in a thread pool, on CPU (no CUDA).
Hypothesis: 7 concurrent CPU encodes of an MoE model oversubscribe the cores and collapse
throughput. This measures 1 vs 7-serial vs 7-concurrent to prove it.

Run:  python -m evals.embed_probe
"""
from __future__ import annotations

import concurrent.futures
import time

from src.core.intelligence.local_embeddings import get_embedding_model


def main() -> None:
    from src.shared.config_manager import ConfigManager
    name = ConfigManager.get("memory.local_embedding_model") or "nomic-ai/nomic-embed-text-v2-moe"
    print(f"embed_probe: model={name}")

    m = get_embedding_model(name)
    t0 = time.perf_counter()
    m.ensure_local_transformer()
    print(f"model load: {time.perf_counter() - t0:.1f}s")

    # torch/device info
    try:
        import torch
        print(f"torch: cuda_available={torch.cuda.is_available()} "
              f"num_threads={torch.get_num_threads()} interop={torch.get_num_interop_threads()}")
    except Exception as e:
        print(f"torch info unavailable: {e}")

    def enc(q: str) -> float:
        t = time.perf_counter()
        m.encode(f"search_query: {q}")
        return time.perf_counter() - t

    # warmup (first encode is slower)
    enc("warmup query about nothing in particular")

    single = enc("how far is the venue from the caterer and what is the price")
    print(f"\nsingle encode: {single*1000:.0f}ms")

    qs = [f"sub intent {i}: catering prices, distance from venue, off-site availability for item {i}"
          for i in range(7)]

    t = time.perf_counter()
    for q in qs:
        enc(q)
    serial = time.perf_counter() - t
    print(f"7 encodes SERIAL:     {serial:.1f}s  ({serial/7*1000:.0f}ms each)")

    t = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=7) as ex:
        list(ex.map(enc, qs))
    conc = time.perf_counter() - t
    print(f"7 encodes CONCURRENT: {conc:.1f}s  (thread pool, 7 wide)")

    print("\n================= VERDICT =================")
    if conc > serial * 1.5:
        print(f"=> CONCURRENT is {conc/serial:.1f}x SLOWER than serial — thread oversubscription "
              f"collapse. The parallel sub-query embedding is the bug: run sub-query embeds "
              f"SERIALLY (or batch-encode in one call), or cap the pool to 1-2.")
    elif serial > 20:
        print(f"=> Embedding itself is just slow on CPU ({serial/7*1000:.0f}ms/encode). "
              f"MoE model on CPU is the cost — switch to a lighter embed model or a smaller "
              f"sub-intent count, and/or batch-encode.")
    else:
        print(f"=> Embedding is fast here (serial {serial:.1f}s, concurrent {conc:.1f}s). "
              f"The 126s is elsewhere (Supabase RPC path / model reload) — re-check.")


if __name__ == "__main__":
    main()
