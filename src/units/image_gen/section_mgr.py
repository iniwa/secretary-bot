"""セクション断片プリセットの JSON→DB 同期。

Phase 3.5e で実運用する初期プリセット（quality/style/negative ほか）を
`section_presets/*.json` に置き、起動時にこのモジュールが `prompt_sections` へ
冪等 upsert する。ユーザーが WebGUI から編集した内容は上書きしない——
is_builtin=1 の行のみ上書き対象。

JSON フォーマット:
    {
      "category_key": "quality",
      "name":         "quality_sdxl_standard",
      "description":  "SDXL 汎用の高品質タグ",
      "positive":     "masterpiece, best quality, amazing quality",
      "negative":     null,
      "tags":         "sdxl,quality"
    }
"""
from __future__ import annotations

import json
import os
from typing import Any

from src.logger import get_logger

log = get_logger(__name__)

_PRESETS_DIR = os.path.join(os.path.dirname(__file__), "section_presets")


class SectionManager:
    def __init__(self, bot) -> None:
        self.bot = bot

    async def sync_presets_to_db(self) -> dict[str, int]:
        """section_presets/*.json を読んで is_builtin=1 で upsert する。
        戻り値は統計 `{"total": N, "updated": N, "skipped": N}`。"""
        stats = {"total": 0, "updated": 0, "skipped": 0}
        if not os.path.isdir(_PRESETS_DIR):
            log.info("section_presets dir not found: %s", _PRESETS_DIR)
            return stats

        for fname in sorted(os.listdir(_PRESETS_DIR)):
            if not fname.endswith(".json"):
                continue
            stats["total"] += 1
            path = os.path.join(_PRESETS_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception as e:
                log.error("section preset load failed %s: %s", fname, e)
                stats["skipped"] += 1
                continue

            if not self._validate_preset(raw, fname):
                stats["skipped"] += 1
                continue

            try:
                await self.bot.database.section_upsert_builtin(
                    category_key=str(raw["category_key"]),
                    name=str(raw["name"]),
                    positive=raw.get("positive"),
                    negative=raw.get("negative"),
                    description=raw.get("description"),
                    tags=raw.get("tags"),
                )
                stats["updated"] += 1
            except Exception as e:
                log.error("section upsert failed %s: %s", fname, e)
                stats["skipped"] += 1

        log.info("section presets synced: %s", stats)
        return stats

    def _validate_preset(self, raw: Any, fname: str) -> bool:
        if not isinstance(raw, dict):
            log.warning("section preset %s is not an object", fname)
            return False
        for key in ("category_key", "name"):
            if not raw.get(key):
                log.warning("section preset %s missing required key: %s", fname, key)
                return False
        if not raw.get("positive") and not raw.get("negative"):
            log.warning("section preset %s has empty positive and negative", fname)
            return False
        return True
