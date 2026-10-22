import unittest
import threading
import sys
from pathlib import Path

# Add root directory to sys.path
root_dir = str(Path(__file__).parents[2])
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from src.core.intelligence.local_embeddings import get_embedding_model

class TestSharedEmbeddings(unittest.TestCase):
    def test_singleton_behavior(self):
        """Verify that get_embedding_model returns the same instance across multiple calls."""
        model1 = get_embedding_model()
        model2 = get_embedding_model()
        self.assertIs(model1, model2)
        
    def test_encode_and_dimension(self):
        """Verify that encoding generates a list of floats with correct dimensions (768)."""
        model = get_embedding_model()
        # Trigger actual encoding
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
