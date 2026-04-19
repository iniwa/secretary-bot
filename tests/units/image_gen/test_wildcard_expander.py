"""wildcard_expander の挙動テスト。

実行: `python -m unittest tests.units.image_gen.test_wildcard_expander`
pytest でもそのまま走る。
"""
import unittest

from src.units.image_gen.wildcard_expander import Choice, expand


class TestWildcardExpander(unittest.TestCase):

    # --- リテラル・エスケープ ---

    def test_plain_text_unchanged(self):
        r = expand("masterpiece, 1girl, best quality", rng_seed=0)
        self.assertEqual(r.text, "masterpiece, 1girl, best quality")
        self.assertEqual(r.choices, [])
        self.assertEqual(r.warnings, [])

    def test_escape_special_chars(self):
        # `\{`, `\|`, `\}`, `\\` がリテラル化される
        r = expand(r"a \{b\|c\} d \\", rng_seed=0)
        self.assertEqual(r.text, "a {b|c} d \\")

    def test_unmatched_brace_is_literal(self):
        r = expand("{abc", rng_seed=0)
        self.assertEqual(r.text, "{abc")
        self.assertEqual(r.choices, [])

    def test_empty_braces_no_choice(self):
        r = expand("a{}b", rng_seed=0)
        self.assertEqual(r.text, "ab")
        self.assertEqual(r.choices, [])

    # --- 均等ランダム ---

    def test_basic_alt_picks_one(self):
        r = expand("{red|green|blue}", rng_seed=42)
        self.assertIn(r.text, {"red", "green", "blue"})
        self.assertEqual(len(r.choices), 1)
        self.assertEqual(r.choices[0].kind, "alt")
        self.assertEqual(r.choices[0].token, "{red|green|blue}")
        self.assertEqual(r.choices[0].picked, r.text)

    def test_alt_distribution_covers_all(self):
        seen: set[str] = set()
        for s in range(200):
            seen.add(expand("{a|b|c}", rng_seed=s).text)
        self.assertEqual(seen, {"a", "b", "c"})

    def test_determinism_same_seed(self):
        a = expand("{w|x|y|z}, {1-1000}", rng_seed=12345)
        b = expand("{w|x|y|z}, {1-1000}", rng_seed=12345)
        self.assertEqual(a.text, b.text)

    def test_different_seeds_can_differ(self):
        results = {expand("{a|b|c|d|e|f|g|h}", rng_seed=s).text for s in range(50)}
        self.assertGreater(len(results), 1)  # 1 種類しか出ないのはバグ

    # --- 重み付き ---

    def test_weighted_skew_heavy(self):
        # w=999 対 w=1 を 300 回回して 95% 以上は a になることを確認
        count_a = sum(
            1 for s in range(300)
            if expand("{999::a|1::b}", rng_seed=s).text == "a"
        )
        self.assertGreaterEqual(count_a, 285)  # 300 × 0.95

    def test_weighted_zero_weight_never_picked(self):
        for s in range(100):
            r = expand("{0::a|1::b}", rng_seed=s)
            self.assertEqual(r.text, "b")

    def test_weighted_all_zero_falls_back_to_uniform(self):
        seen = {expand("{0::a|0::b}", rng_seed=s).text for s in range(100)}
        self.assertEqual(seen, {"a", "b"})

    def test_weight_integer_and_float(self):
        # `2::a` と `1.5::b` が共にパースできる
        r = expand("{2::a|1.5::b}", rng_seed=0)
        self.assertIn(r.text, {"a", "b"})

    def test_escaped_colon_is_literal(self):
        # `\:` で `:` をエスケープ → 重み記法として解釈されない
        r = expand(r"{2\:\:hello|world}", rng_seed=0)
        self.assertIn(r.text, {"2::hello", "world"})

    # --- 連番 ---

    def test_range_inclusive(self):
        for s in range(50):
            n = int(expand("{1-5}", rng_seed=s).text)
            self.assertGreaterEqual(n, 1)
            self.assertLessEqual(n, 5)

    def test_range_reversed_order_ok(self):
        seen = {expand("{5-1}", rng_seed=s).text for s in range(80)}
        self.assertEqual(seen, {"1", "2", "3", "4", "5"})

    def test_range_records_choice(self):
        r = expand("{1-3}", rng_seed=0)
        self.assertEqual(len(r.choices), 1)
        self.assertEqual(r.choices[0].kind, "range")
        self.assertEqual(r.choices[0].token, "{1-3}")

    def test_range_negative(self):
        seen = {int(expand("{-2--1}", rng_seed=s).text) for s in range(30)}
        self.assertLessEqual(seen, {-2, -1})

    # --- ファイル参照 ---

    def test_file_pick(self):
        files = {"hair": "red\nblue\ngreen"}
        seen = {expand("__hair__", files=files, rng_seed=s).text for s in range(60)}
        self.assertEqual(seen, {"red", "blue", "green"})

    def test_file_ignores_comments_and_blanks(self):
        files = {"pose": "# header\n\nstanding\n  # indent is NOT comment\nsitting\n\n"}
        seen = {expand("__pose__", files=files, rng_seed=s).text for s in range(50)}
        # `# header` と空行は除外、`  # indent ...` は strip 後 `# ...` なのでコメント扱い
        self.assertEqual(seen, {"standing", "sitting"})

    def test_file_missing_is_literal_with_warning(self):
        r = expand("hair: __unknown__", files={}, rng_seed=0)
        self.assertEqual(r.text, "hair: __unknown__")
        self.assertTrue(any("未定義" in w for w in r.warnings))
        self.assertEqual(r.choices, [])

    def test_file_empty_is_literal_with_warning(self):
        r = expand("__empty__", files={"empty": "# only comment\n\n"}, rng_seed=0)
        self.assertEqual(r.text, "__empty__")
        self.assertTrue(any("有効な行が無い" in w for w in r.warnings))

    def test_file_escape_blocks_expansion(self):
        # `\_` で先頭の `_` をリテラル化 → `__name__` として解釈されない
        r = expand(r"\__name__", files={"name": "picked"}, rng_seed=0)
        self.assertEqual(r.text, "__name__")
        self.assertEqual(r.choices, [])

    def test_file_choice_source_annotated(self):
        r = expand("__hair__", files={"hair": "red"}, rng_seed=0)
        self.assertEqual(len(r.choices), 1)
        c = r.choices[0]
        self.assertEqual(c.kind, "file")
        self.assertEqual(c.token, "__hair__")
        self.assertEqual(c.picked, "red")
        self.assertEqual(c.source, "file:hair")

    # --- 組み合わせ / choices 記録 ---

    def test_multiple_tokens_all_recorded(self):
        files = {"hair": "red\nblue"}
        r = expand(
            "{1girl|1boy}, __hair__ hair, {1-3} years",
            files=files, rng_seed=7,
        )
        self.assertEqual(len(r.choices), 3)
        kinds = [c.kind for c in r.choices]
        self.assertEqual(kinds, ["alt", "file", "range"])

    def test_no_nested_expansion_of_substituted_content(self):
        # ファイル内容に `{x|y}` が入っていても再展開しない（入れ子は v1 非対応）
        files = {"tmpl": "{x|y}"}
        r = expand("__tmpl__", files=files, rng_seed=0)
        self.assertEqual(r.text, "{x|y}")

    def test_nested_braces_first_close_wins(self):
        # `{a|{b|c}}` → 最初の `}` で閉じるので inner=`a|{b|c`、残り `}` はリテラル
        # pipe 分割は入れ子非対応のため alts = ['a', '{b', 'c']
        seen = {expand("{a|{b|c}}", rng_seed=s).text for s in range(60)}
        self.assertEqual(seen, {"a}", "{b}", "c}"})


if __name__ == "__main__":
    unittest.main()
