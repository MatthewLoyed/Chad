import unittest
import threading
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

# Add root directory to sys.path
root_dir = str(Path(__file__).parents[2])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.core.intelligence.local_embeddings import get_embedding_model, reset_embedding_model_singleton


class _ScriptedTransformer:
    """Fail on concurrent encode: the wrapper must serialize non-thread-safe models."""
    active = False

    def encode(self, texts, batch_size):
        if self.active:
            raise AssertionError("Concurrent transformer access")
        self.active = True
        try:
            time.sleep(0.005)  # Release the GIL so concurrent callers contend.
            return [[0.5] * 768 for _ in texts]
        finally:
            self.active = False

class TestSharedEmbeddings(unittest.TestCase):
    def setUp(self):
        reset_embedding_model_singleton()
        self.addCleanup(reset_embedding_model_singleton)
        stub = SimpleNamespace(SentenceTransformer=lambda *args, **kwargs: _ScriptedTransformer())
        modules = patch.dict(sys.modules, {"sentence_transformers": stub})
        modules.start()
        self.addCleanup(modules.stop)

    def test_singleton_behavior(self):
        """Verify that get_embedding_model returns the same instance across multiple calls."""
        model1 = get_embedding_model()
        model2 = get_embedding_model()
        self.assertIs(model1, model2)
        
    def test_encode_and_dimension(self):
        """Verify that encoding generates a list of floats with correct dimensions (768)."""
        model = get_embedding_model()
        # Exercise wrapper conversion with a scripted transformer; unit tests
        # must not depend on cached weights or initiate optional downloads.
        emb = model.encode("search_query: Hello World")
        self.assertIsInstance(emb, list)
        self.assertTrue(all(isinstance(x, float) for x in emb))
        self.assertEqual(len(emb), 768)

    def test_concurrent_encoding_thread_safety(self):
        """Verify thread-safety when multiple threads encode concurrently."""
        model = get_embedding_model()
        results = []
        errors = []

        def worker(idx):
            try:
                emb = model.encode(f"search_query: Thread test sentence {idx}")
                results.append(emb)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Thread errors occurred: {errors}")
        self.assertEqual(len(results), 5)
        for r in results:
            self.assertEqual(len(r), 768)

if __name__ == "__main__":
    unittest.main()
