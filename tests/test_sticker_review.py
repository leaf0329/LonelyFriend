from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_stickers import apply_manual_review


class ManualStickerReviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {"collected_on": "2026-10-26", "stickers": [
            {"id": "a", "review": {"status": "approved"}},
            {"id": "b", "review": {"status": "reserved"}},
            {"id": "c", "review": {"status": "reserved"}},
        ]}
        self.review = {"schema": 1, "collected_on": "2026-10-26", "exported_at": "2026-10-26T15:52:26Z",
                       "choices": {"a": "exclude", "b": "keep"}, "unmarked": "keep"}

    def test_explicit_exclusion_wins_and_unmarked_reserved_images_are_kept(self):
        initial = deepcopy(self.catalog)
        result = apply_manual_review(self.catalog, self.review)
        self.assertEqual([x["review"]["status"] for x in result["stickers"]],
                         ["reserved", "approved", "approved"])
        self.assertEqual(result["manual_review"]["available"], 2)
        self.assertEqual(self.catalog, initial)

    def test_initial_rule_does_not_approve_unmarked_images(self):
        self.review["unmarked"] = "initial"
        result = apply_manual_review(self.catalog, self.review)
        self.assertEqual(result["stickers"][2]["review"]["status"], "reserved")

    def test_mismatched_collection_or_invalid_decisions_fail_before_application(self):
        for change in ({"collected_on": "2026-10-25"}, {"choices": {"unknown": "exclude"}},
                       {"choices": {"a": "surprise"}}, {"unmarked": "guess"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                apply_manual_review(self.catalog, {**self.review, **change})


if __name__ == "__main__":
    unittest.main()
