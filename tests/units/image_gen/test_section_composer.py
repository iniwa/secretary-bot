"""section_composer の挙動テスト。

実行: `python -m unittest tests.units.image_gen.test_section_composer`
pytest でもそのまま走る。
"""
import unittest

from src.units.image_gen.section_composer import (
    ComposedTag,
    ComposeResult,
    SectionInput,
    compose_prompt,
)


def _sec(id_: int, category: str, pos: str | None = None, neg: str | None = None) -> dict:
    return {"id": id_, "category_key": category, "positive": pos, "negative": neg}


class TestSectionComposer(unittest.TestCase):

    def test_empty_input(self):
        r = compose_prompt([])
        self.assertEqual(r.positive, "")
        self.assertEqual(r.negative, "")
        self.assertEqual(r.warnings, [])

    def test_single_section_basic(self):
        r = compose_prompt([_sec(1, "quality", "masterpiece, best quality")])
        self.assertEqual(r.positive, "masterpiece, best quality")
        self.assertEqual(len(r.positive_tags), 2)

    def test_multiple_sections_ordered(self):
        r = compose_prompt([
            _sec(1, "quality", "masterpiece"),
            _sec(2, "style",   "anime style"),
            _sec(3, "character", "1girl, blue eyes"),
        ])
        self.assertEqual(r.positive, "masterpiece, anime style, 1girl, blue eyes")

    def test_duplicate_first_wins(self):
        r = compose_prompt([
            _sec(1, "quality", "masterpiece, 1girl"),
            _sec(2, "character", "1girl, solo"),
        ])
        self.assertEqual(r.positive, "masterpiece, 1girl, solo")
        self.assertIn("1girl", r.dropped)

    def test_weight_conflict_warns_and_first_wins(self):
        r = compose_prompt([
            _sec(1, "quality", "(masterpiece:1.2)"),
            _sec(2, "style",   "(masterpiece:1.4)"),
        ])
        self.assertEqual(r.positive, "(masterpiece:1.2)")
        self.assertTrue(any("weight" in w for w in r.warnings))

    def test_negative_independent_pipeline(self):
        r = compose_prompt([
            _sec(1, "quality",  "masterpiece", "lowres"),
            _sec(2, "negative", None,          "bad anatomy, lowres"),
        ])
        self.assertEqual(r.positive, "masterpiece")
        # lowres が先勝ちで 1 回だけ
        self.assertEqual(r.negative, "lowres, bad anatomy")

    def test_case_and_whitespace_normalized(self):
        r = compose_prompt([
            _sec(1, "quality", "Masterpiece,  best  quality"),
            _sec(2, "style",   "masterpiece, BEST QUALITY, detailed"),
        ])
        # 1 つ目の raw を保持、重複は dropped
        self.assertEqual(r.positive, "Masterpiece, best  quality, detailed")
        self.assertEqual(len(r.dropped), 2)

    def test_user_positive_tail_default(self):
        r = compose_prompt(
            [_sec(1, "quality", "masterpiece")],
            user_positive="extra detail",
        )
        self.assertEqual(r.positive, "masterpiece, extra detail")

    def test_user_positive_head(self):
        r = compose_prompt(
            [_sec(1, "quality", "masterpiece")],
            user_positive="1girl",
            user_position="head",
        )
        self.assertEqual(r.positive, "1girl, masterpiece")

    def test_user_positive_before_category(self):
        r = compose_prompt(
            [
                _sec(1, "quality",   "masterpiece"),
                _sec(2, "character", "1girl"),
                _sec(3, "composition", "from above"),
            ],
            user_positive="blue hair",
            user_position="section:character",
        )
        # character の直前に挿入
        self.assertEqual(r.positive, "masterpiece, blue hair, 1girl, from above")

    def test_user_position_unknown_category_falls_back_to_tail(self):
        r = compose_prompt(
            [_sec(1, "quality", "masterpiece")],
            user_positive="1girl",
            user_position="section:nonexistent",
        )
        self.assertEqual(r.positive, "masterpiece, 1girl")

    def test_weighted_tag_preserves_raw(self):
        r = compose_prompt([_sec(1, "quality", "(best quality:1.2), masterpiece")])
        self.assertEqual(r.positive, "(best quality:1.2), masterpiece")
        self.assertAlmostEqual(r.positive_tags[0].weight, 1.2)

    def test_empty_and_whitespace_tags_skipped(self):
        r = compose_prompt([_sec(1, "quality", "masterpiece,  , ,best")])
        self.assertEqual(r.positive, "masterpiece, best")

    def test_user_negative_independent(self):
        r = compose_prompt(
            [_sec(1, "negative", None, "lowres")],
            user_negative="bad anatomy",
        )
        self.assertEqual(r.negative, "lowres, bad anatomy")


if __name__ == "__main__":
    unittest.main()
