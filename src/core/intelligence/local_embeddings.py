import threading
import time
from typing import List, Optional, Union

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger

logger = setup_logger("LocalEmbeddings")

# Module-level singletons
_shared_model = None
_shared_model_lock = threading.Lock()


class LocalEmbeddingModel:
    """
    Stateless, thread-safe wrapper for local text embeddings.
    Prefers LM Studio / Ollama OpenAI-compatible ``/embeddings`` when
    ``embeddings.use_api`` is true (GPU accel on AMD via LM Studio); falls back
    to in-process SentenceTransformer (CPU on machines without CUDA).
    """

    def __init__(
        self,
        model_name: str,
        base_url: Optional[str] = None,
        api_model_name: Optional[str] = None,
    ):
        # HF id for SentenceTransformer fallback (e.g. nomic-ai/nomic-embed-text-v2-moe)
        self.model_name = model_name
        # LM Studio / Ollama served id (e.g. text-embedding-nomic-embed-text-v2-moe)
        self.api_model_name = api_model_name or model_name
        self.base_url = base_url or "http://localhost:1234/v1"
        self._local_transformer = None
        self._local_transformer_lock = threading.Lock()
        self._is_api_checked = False
        self._use_api = False
        self._api_check_lock = threading.Lock()

    def encode(self, text: Union[str, List[str]]) -> Union[List[float], List[List[float]]]:
        """
        Generate embeddings. Returns a list of floats (if input is str) or a list of lists of floats.
        """
        if isinstance(text, str):
            texts = [text]
            single = True
        else:
            texts = list(text)
            single = False

        if not texts:
            return [] if single else []

        self._maybe_enable_api()

        if self._use_api:
            try:
                embeddings = self._encode_via_api(texts)
                return embeddings[0] if single else embeddings
            except Exception as e:
                logger.warning(
                    f"[ EMBEDDINGS ] API embedding failed, falling back to local model: {e}"
                )
                self._use_api = False

        # Fallback to local SentenceTransformer (typically CPU on AMD)
        self.ensure_local_transformer()
        with self._local_transformer_lock:
            embeddings = self._local_transformer.encode(texts, batch_size=min(64, max(1, len(texts))))

        if hasattr(embeddings, "tolist"):
            result = embeddings.tolist()
        else:
            result = [list(e) for e in embeddings]
        return result[0] if single else result

    def _maybe_enable_api(self) -> None:
        if not ConfigManager.get("embeddings.use_api", False):
            self._is_api_checked = True
            self._use_api = False
            return
        if not self._is_api_checked:
            with self._api_check_lock:
                if not self._is_api_checked:
                    self._check_api_availability()

    def _check_api_availability(self):
        """Check if LM Studio / Ollama embeddings endpoint is active."""
        self._is_api_checked = True
        url = f"{self.base_url.rstrip('/')}/embeddings"
        try:
            payload = {"input": ["test"], "model": self.api_model_name}
            # Cold load of an embed model can take a few seconds
            timeout = float(ConfigManager.get("embeddings.api_check_timeout_seconds", 8) or 8)
            logger.debug(f"[ EMBEDDINGS ] Checking API availability at {url} model={self.api_model_name}...")
            response = httpx.post(url, json=payload, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and len(data["data"]) > 0:
                    self._use_api = True
                    logger.warning(
                        f"[ SUCCESS ] Embeddings API online at {self.base_url} "
                        f"(model={self.api_model_name}). Using LM Studio/Ollama GPU path."
                    )
                    return
            logger.warning(
                f"[ EMBEDDINGS ] API check HTTP {response.status_code}; using SentenceTransformer."
            )
        except Exception as e:
            logger.warning(
                f"[ EMBEDDINGS ] Local API embeddings unavailable ({e}). "
                f"Fallback to SentenceTransformer."
            )

    def _encode_via_api(self, texts: List[str]) -> List[List[float]]:
        """Query OpenAI-compatible local embeddings API, chunking large batches."""
        url = f"{self.base_url.rstrip('/')}/embeddings"
        batch_size = int(ConfigManager.get("embeddings.batch_size", 64) or 64)
        batch_size = max(1, batch_size)
        timeout = float(ConfigManager.get("embeddings.api_timeout_seconds", 120) or 120)

        out: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            payload = {"input": chunk, "model": self.api_model_name}
            response = httpx.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            results = data["data"]
            results.sort(key=lambda x: x["index"])
            out.extend(item["embedding"] for item in results)
        return out

    def ensure_local_transformer(self):
        """Lazy-load the SentenceTransformer model in a thread-safe manner."""
        if self._local_transformer is None:
            with self._local_transformer_lock:
                if self._local_transformer is None:
                    logger.warning(
                        f"[ EMBEDDINGS ] Loading local SentenceTransformer model: {self.model_name}..."
                    )
                    start_time = time.time()
                    try:
                        from sentence_transformers import SentenceTransformer

                        self._local_transformer = SentenceTransformer(
                            self.model_name, trust_remote_code=True,
                            local_files_only=not ConfigManager.get("embeddings.allow_download", False),
                        )
                        logger.warning(
                            f"[ SUCCESS ] Loaded local SentenceTransformer. "
                            f"(took {time.time() - start_time:.2f}s)"
                        )
                    except Exception as e:
                        logger.error(
                            f"[ ERROR ] Failed to load local SentenceTransformer: {e}",
                            exc_info=True,
                        )
                        raise e


def reset_embedding_model_singleton() -> None:
    """Clear the process-wide singleton (tests / config changes)."""
    global _shared_model
    with _shared_model_lock:
        _shared_model = None


def get_embedding_model(model_name: Optional[str] = None) -> LocalEmbeddingModel:
    """
    Retrieve or initialize the thread-safe global shared embedding model instance.
    """
    global _shared_model
    if _shared_model is None:
        with _shared_model_lock:
            if _shared_model is None:
                hf_name = model_name or ConfigManager.get(
                    "memory.local_embedding_model",
                    "nomic-ai/nomic-embed-text-v2-moe",
                )
                api_name = ConfigManager.get("embeddings.api_model") or (
                    "text-embedding-nomic-embed-text-v2-moe"
                    if ConfigManager.get("embeddings.use_api", False)
                    else hf_name
                )
                base_url = (
                    ConfigManager.get("embeddings.base_url")
                    or ConfigManager.get("local_models.active_model.base_url")
                    or ConfigManager.get("local_models.providers.lmstudio.base_url")
                    or "http://localhost:1234/v1"
                )
                _shared_model = LocalEmbeddingModel(hf_name, base_url, api_model_name=api_name)
    return _shared_model
