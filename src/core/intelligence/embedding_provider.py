"""
EmbeddingProvider — text embedding generation for the memory subsystem.

Extracted from MemoryEngine (refs #246) so embedding strategy (cloud Gemini
vs. local Nomic v2 MoE) is a single-responsibility collaborator the facade
composes rather than owns. Selection is driven by config
`memory.embedding_strategy` (cloud | local | hybrid) with automatic local
fallback when the cloud key/SDK is unavailable.
"""

from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger("MemoryEngine")

# Modern GenAI client used for cloud embeddings (lazy-initialized, process-wide).
_genai_client = None

# Local SentenceTransformer for fallback / privacy-mode (lazy, process-wide).
_local_embedding_model = None

# Only warn once about missing cloud dependencies/auth.
_warned_genai_v2 = False


class EmbeddingProvider:
    """Generates embeddings via cloud (Gemini) or local (Nomic) models."""

    def __init__(
        self,
        embedding_strategy: str = "cloud",
        local_model_name: str = "nomic-ai/nomic-embed-text-v2-moe",
        api_key: Optional[str] = None,
    ):
        self.embedding_strategy = embedding_strategy
        self.local_model_name = local_model_name
        self.api_key = api_key

    def _ensure_local_embedding_model(self):
        """Lazy load local embedding wrapper (LM Studio API and/or SentenceTransformer)."""
        global _local_embedding_model
        if _local_embedding_model is None:
            from src.core.intelligence.local_embeddings import get_embedding_model

            _local_embedding_model = get_embedding_model(self.local_model_name)
            # Do NOT force SentenceTransformer load — encode() will use LM Studio when
            # embeddings.use_api is true (needed on AMD where CUDA torch is unavailable).

    def get_embeddings(self, texts: List[str]) -> Optional[List[Optional[List[float]]]]:
        """Batch-embed texts. Returns one vector per input, or None on total failure."""
        if not texts:
            return []
        if self.embedding_strategy == "local" or (
            not self.api_key and self.embedding_strategy != "cloud"
        ):
            self._ensure_local_embedding_model()
            if not _local_embedding_model:
                return None
            try:
                embs = _local_embedding_model.encode(texts)
                if isinstance(embs, list) and embs and isinstance(embs[0], (int, float)):
                    # Single vector returned for 1-item list edge cases
                    return [list(embs)]
                out: List[Optional[List[float]]] = []
                for emb in embs:
                    if emb is None:
                        out.append(None)
                    elif hasattr(emb, "tolist"):
                        out.append(emb.tolist())
                    else:
                        out.append(list(emb))
                return out
            except Exception as e:
                logger.error(f"[ MEMORY ] Local batch embedding failed: {e}")
                if self.embedding_strategy == "local":
                    return None
        # Fall back to per-item for cloud/hybrid (rare for ingest)
        return [self.get_embedding(t) for t in texts]

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding using either Cloud (Gemini) or Local (Nomic) models.
        Preference determined by config.memory.embedding_strategy.
        """
        global _genai_client, _warned_genai_v2, _local_embedding_model

        # 1. LOCAL Strategy (Default / Privacy Mode)
        # We prefer Nomic v2 MoE via Sentence-Transformers or LM Studio for all embeddings
        if self.embedding_strategy == "local" or (not self.api_key and self.embedding_strategy != "cloud"):
            self._ensure_local_embedding_model()
            if _local_embedding_model:
                try:
                    emb = _local_embedding_model.encode(text)
                    return emb.tolist() if hasattr(emb, 'tolist') else list(emb)
                except Exception as e:
                    logger.error(f"[ MEMORY ] Local embedding failed: {e}")

            if self.embedding_strategy == "local":
                return None

        # 2. CLOUD Strategy (Gemini)
        # DEPRECATED: We are migrating away from Gemini embeddings for local personal data
        if not self.api_key:
            if not _warned_genai_v2:
                logger.warning("[ MEMORY ] API Key missing for Gemini embeddings. Falling back to local Nomic v2 MoE.")
                _warned_genai_v2 = True

            # Auto-fallback to local if cloud fails/missing key
            self._ensure_local_embedding_model()
            if _local_embedding_model:
                try:
                    emb = _local_embedding_model.encode(text)
                    return emb.tolist() if hasattr(emb, 'tolist') else list(emb)
                except Exception:
                    pass
            return None

        try:
            # Lazy initialize the modern GenAI client
            if _genai_client is None:
                from google import genai
                _genai_client = genai.Client(api_key=self.api_key)

            # Try state-of-the-art Gemini Embedding 2 with dimensionality truncation to match schema
            try:
                result = _genai_client.models.embed_content(
                    model="models/gemini-embedding-2-preview",
                    contents=text,
                    config={
                        'task_type': 'RETRIEVAL_DOCUMENT',
                        'output_dimensionality': 768
                    }
                )
            except Exception as preview_err:
                logger.warning(f"[ MEMORY ] Gemini preview model failed, falling back to local Nomic: {preview_err}")
                self._ensure_local_embedding_model()
                if _local_embedding_model:
                    emb = _local_embedding_model.encode(text)
                    return emb.tolist() if hasattr(emb, 'tolist') else list(emb)
                return None

            # Result format is different in modern SDK (result.embeddings[0].values)
            if hasattr(result, 'embeddings') and result.embeddings:
                return result.embeddings[0].values
            return None

        except ImportError:
            if not _warned_genai_v2:
                logger.warning("[ MEMORY ] google-genai SDK not installed. Run 'pip install google-genai'.")
                _warned_genai_v2 = True
            return None
        except Exception as e:
            if not _warned_genai_v2:
                logger.error(f"[ MEMORY ] Embedding error: {e}")
                _warned_genai_v2 = True
            return None
