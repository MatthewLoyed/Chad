"""Reference checks must fail on missing evidence rather than infer success."""
import io
import json
import unittest
from unittest.mock import patch

from evals.project_context_probe import ROOT, SCENARIOS, assess_answer, orientation, reader_results, structural_results


class ProjectContextTests(unittest.TestCase):
    def test_five_existing_routes_resolve(self):
        results = structural_results(ROOT)
        self.assertEqual(len(results), 5)
        self.assertTrue(all(r.passed for r in results), [r.assertion_failures for r in results])

    def test_vague_assurances_do_not_pass_reference_checks(self):
        for case in SCENARIOS:
            self.assertTrue(assess_answer(case, "I understand and will do the right thing."))
            answer = " ".join(case["refs"] + [case["check"], case["constraint"]])
            self.assertEqual(assess_answer(case, answer), [])

    def test_orientation_budget_is_explicit(self):
        text = orientation(ROOT, None, 1000)
        self.assertEqual(len(text), 1000)
        self.assertTrue(text.startswith("FILE AGENTS.md"))

    def test_reader_records_usage_and_requires_manual_review(self):
        answer = " ".join(part for case in SCENARIOS for part in
                          case["refs"] + [case["check"], case["constraint"]])
        payload = json.dumps({"choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
                              "usage": {"total_tokens": 17}}).encode()
        responses = [io.BytesIO(payload) for _ in SCENARIOS]
        with patch("evals.project_context_probe.urlopen", side_effect=responses):
            results = reader_results(ROOT, "fixture-model", None, 1000)
        self.assertTrue(all(r.passed for r in results))
        self.assertTrue(all(r.transcript["manual_review_required"] for r in results))
        self.assertTrue(all(r.metrics["total_tokens"] == 17 for r in results))

    def test_reader_transport_errors_are_failures(self):
        with patch("evals.project_context_probe.urlopen", side_effect=TimeoutError("not available")):
            results = reader_results(ROOT, "fixture-model", None, 1000)
        self.assertTrue(all(not r.passed and r.error for r in results))


if __name__ == "__main__":
    unittest.main()
