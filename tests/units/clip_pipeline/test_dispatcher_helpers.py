"""Dispatcher モジュールの純粋関数テスト。

`_compute_backoff` / `_future` / `_parse_json` / `_normalize_result_paths`
を対象。外部 I/O 無しで完結する。
"""
from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone

from src.units.clip_pipeline import dispatcher as disp


JST = timezone(timedelta(hours=9))


class TestComputeBackoff(unittest.TestCase):
    def test_doubles_each_retry_until_cap(self):
        # base=10, cap=100
        # retry 0 → 10, 1 → 20, 2 → 40, 3 → 80, 4 → 100(capped)
        # jitter は ±10% なので幅を見て確認
        for retry, expected in [(0, 10), (1, 20), (2, 40), (3, 80)]:
            low, high = expected * 0.9, expected * 1.1
            for _ in range(10):
                v = disp._compute_backoff(retry, 10.0, 100.0)
                self.assertGreaterEqual(v, min(low, 5.0))
                self.assertLessEqual(v, high + 0.001)

    def test_cap_is_enforced(self):
        for _ in range(20):
            v = disp._compute_backoff(10, 30.0, 60.0)
            # cap=60, jitter ±10% で上限は 66
            self.assertLessEqual(v, 66.0 + 0.001)

    def test_minimum_floor(self):
        # 極小 base でも最小 5.0 を下回らない
        for _ in range(10):
            v = disp._compute_backoff(0, 0.1, 10.0)
            self.assertGreaterEqual(v, 5.0)


class TestFuture(unittest.TestCase):
    def test_returns_jst_string_near_now_plus_delta(self):
        out = disp._future(60)
        # フォーマット検査
        self.assertRegex(out, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        parsed = datetime.strptime(out, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
        expected = datetime.now(JST) + timedelta(seconds=60)
        self.assertLess(abs((parsed - expected).total_seconds()), 5)


class TestParseJson(unittest.TestCase):
    def test_parses_valid_json(self):
        self.assertEqual(disp._parse_json('{"a": 1}', {}), {"a": 1})
        self.assertEqual(disp._parse_json("[1,2,3]", []), [1, 2, 3])

    def test_fallback_on_invalid(self):
        self.assertEqual(disp._parse_json("not-json", {"x": 1}), {"x": 1})
        self.assertEqual(disp._parse_json(None, {}), {})
        self.assertEqual(disp._parse_json("", []), [])


class TestNormalizeResultPaths(unittest.TestCase):
    NAS = ("/mnt/secretary-bot/auto-kirinuki", "outputs")

    def test_windows_drive_letter_to_posix(self):
        r = disp._normalize_result_paths({
            "transcript_path": r"N:\auto-kirinuki\outputs\stream01\transcript.json",
            "edl_path":        r"N:\auto-kirinuki\outputs\stream01\timeline.edl",
            "highlights_path": r"N:\auto-kirinuki\outputs\stream01\highlights.json",
            "clip_paths": [
                r"N:\auto-kirinuki\outputs\stream01\clips\c1.mp4",
                r"N:\auto-kirinuki\outputs\stream01\clips\c2.mp4",
            ],
            "highlights_count": 2,  # 非パス項目は保持
        }, self.NAS)
        self.assertEqual(
            r["transcript_path"],
            "/mnt/secretary-bot/auto-kirinuki/outputs/stream01/transcript.json",
        )
        self.assertEqual(
            r["edl_path"],
            "/mnt/secretary-bot/auto-kirinuki/outputs/stream01/timeline.edl",
        )
        self.assertEqual(len(r["clip_paths"]), 2)
        self.assertTrue(r["clip_paths"][0].startswith(
            "/mnt/secretary-bot/auto-kirinuki/outputs/stream01/clips/"
        ))
        self.assertEqual(r["highlights_count"], 2)

    def test_already_pi_path_passthrough(self):
        src = {
            "transcript_path": "/mnt/secretary-bot/auto-kirinuki/outputs/s1/transcript.json",
            "clip_paths": [],
        }
        r = disp._normalize_result_paths(src, self.NAS)
        self.assertEqual(r["transcript_path"], src["transcript_path"])

    def test_non_string_path_passthrough(self):
        # 異形（None / 欠落）でも落ちない
        r = disp._normalize_result_paths({
            "transcript_path": None,
            "edl_path": "",
        }, self.NAS)
        self.assertIsNone(r["transcript_path"])
        self.assertEqual(r["edl_path"], "")
