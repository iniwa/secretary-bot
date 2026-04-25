"""セクション合成プリセット (prompt_section_categories / prompt_sections / prompt_section_presets) 関連のDBメソッド。"""

from src.database._base import jst_now


class SectionMixin:
    # === セクション合成プリセット: prompt_section_categories / prompt_sections ===

    async def section_category_list(self) -> list[dict]:
        return await self.fetchall(
            "SELECT * FROM prompt_section_categories "
            "ORDER BY display_order ASC, id ASC"
        )

    async def section_category_get(self, key: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM prompt_section_categories WHERE key = ?", (key,),
        )

    async def section_category_insert(
        self, *, key: str, label: str,
        description: str | None = None,
        display_order: int = 500,
    ) -> int:
        """ユーザー追加カテゴリ（is_builtin=0）を作成。"""
        cursor = await self.execute(
            "INSERT INTO prompt_section_categories "
            "(key, label, description, display_order, is_builtin) "
            "VALUES (?, ?, ?, ?, 0)",
            (key, label, description, display_order),
        )
        return cursor.lastrowid

    async def section_category_update(
        self, key: str, *, label: str | None = None,
        description: str | None = None,
        display_order: int | None = None,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        if label is not None:
            sets.append("label = ?"); params.append(label)
        if description is not None:
            sets.append("description = ?"); params.append(description)
        if display_order is not None:
            sets.append("display_order = ?"); params.append(display_order)
        if not sets:
            return False
        params.append(key)
        rowcount = await self.execute_returning_rowcount(
            f"UPDATE prompt_section_categories SET {', '.join(sets)} WHERE key = ?",
            tuple(params),
        )
        return rowcount == 1

    async def section_category_delete(self, key: str) -> bool:
        """ユーザー追加カテゴリのみ削除可能（is_builtin=1 は False 返却）。
        紐づくセクションも CASCADE 的に削除する。"""
        row = await self.section_category_get(key)
        if not row or row["is_builtin"]:
            return False
        await self.execute("DELETE FROM prompt_sections WHERE category_key = ?", (key,))
        rowcount = await self.execute_returning_rowcount(
            "DELETE FROM prompt_section_categories WHERE key = ? AND is_builtin = 0",
            (key,),
        )
        return rowcount == 1

    async def section_list(
        self, category_key: str | None = None,
        starred_only: bool = False,
    ) -> list[dict]:
        conditions: list[str] = []
        params: list = []
        if category_key:
            conditions.append("category_key = ?"); params.append(category_key)
        if starred_only:
            conditions.append("starred = 1")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        return await self.fetchall(
            f"SELECT * FROM prompt_sections{where} "
            f"ORDER BY category_key ASC, starred DESC, name ASC",
            tuple(params),
        )

    async def section_get(self, section_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM prompt_sections WHERE id = ?", (section_id,),
        )

    async def section_get_many(self, section_ids: list[int]) -> list[dict]:
        """section_ids の順序を保ったまま返す（合成順に使う）。"""
        if not section_ids:
            return []
        placeholders = ",".join("?" * len(section_ids))
        rows = await self.fetchall(
            f"SELECT * FROM prompt_sections WHERE id IN ({placeholders})",
            tuple(section_ids),
        )
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in section_ids if i in by_id]

    async def section_insert(
        self, *, category_key: str, name: str,
        positive: str | None = None, negative: str | None = None,
        description: str | None = None, tags: str | None = None,
        is_builtin: int = 0, starred: int = 0,
    ) -> int:
        cursor = await self.execute(
            "INSERT INTO prompt_sections "
            "(category_key, name, description, positive, negative, tags, is_builtin, starred, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (category_key, name, description, positive, negative, tags,
             int(is_builtin), int(starred), jst_now(), jst_now()),
        )
        return cursor.lastrowid

    async def section_update(
        self, section_id: int, **fields,
    ) -> bool:
        allowed = {"name", "description", "positive", "negative", "tags", "starred", "category_key"}
        sets: list[str] = []
        params: list = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            sets.append(f"{k} = ?"); params.append(v)
        if not sets:
            return False
        sets.append("updated_at = ?"); params.append(jst_now())
        params.append(section_id)
        rowcount = await self.execute_returning_rowcount(
            f"UPDATE prompt_sections SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return rowcount == 1

    async def section_delete(self, section_id: int) -> bool:
        row = await self.section_get(section_id)
        if not row or row["is_builtin"]:
            return False
        rowcount = await self.execute_returning_rowcount(
            "DELETE FROM prompt_sections WHERE id = ? AND is_builtin = 0",
            (section_id,),
        )
        return rowcount == 1

    async def section_upsert_builtin(
        self, *, category_key: str, name: str,
        positive: str | None, negative: str | None,
        description: str | None, tags: str | None,
    ) -> int:
        """section_mgr が起動時に JSON プリセットを sync するための冪等 upsert。
        既存行は positive/negative/description/tags を上書き、is_builtin=1 を維持。"""
        existing = await self.fetchone(
            "SELECT id FROM prompt_sections WHERE category_key = ? AND name = ?",
            (category_key, name),
        )
        if existing:
            await self.execute(
                "UPDATE prompt_sections "
                "SET positive = ?, negative = ?, description = ?, tags = ?, "
                "    is_builtin = 1, updated_at = ? "
                "WHERE id = ?",
                (positive, negative, description, tags, jst_now(), existing["id"]),
            )
            return existing["id"]
        return await self.section_insert(
            category_key=category_key, name=name,
            positive=positive, negative=negative,
            description=description, tags=tags,
            is_builtin=1,
        )

    # === セクションプリセット: prompt_section_presets ===

    async def section_preset_list(self, *, include_nsfw: bool = True) -> list[dict]:
        """include_nsfw=False で is_nsfw=1 の行を除外（NSFWモードOFF時用）。"""
        where = "" if include_nsfw else " WHERE is_nsfw = 0"
        return await self.fetchall(
            f"SELECT * FROM prompt_section_presets{where} ORDER BY name ASC",
        )

    async def section_preset_get(self, preset_id: int) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM prompt_section_presets WHERE id = ?", (preset_id,),
        )

    async def section_preset_get_by_name(self, name: str) -> dict | None:
        return await self.fetchone(
            "SELECT * FROM prompt_section_presets WHERE name = ?", (name,),
        )

    async def section_preset_insert(
        self, *, name: str, payload_json: str, description: str | None = None,
        is_nsfw: bool = False,
    ) -> int:
        cursor = await self.execute(
            "INSERT INTO prompt_section_presets "
            "(name, description, payload_json, is_nsfw, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, description, payload_json, 1 if is_nsfw else 0, jst_now(), jst_now()),
        )
        return cursor.lastrowid

    async def section_preset_update(
        self, preset_id: int, *,
        name: str | None = None, description: str | None = None,
        payload_json: str | None = None,
        is_nsfw: bool | None = None,
    ) -> bool:
        sets: list[str] = []
        params: list = []
        if name is not None:
            sets.append("name = ?"); params.append(name)
        if description is not None:
            sets.append("description = ?"); params.append(description)
        if payload_json is not None:
            sets.append("payload_json = ?"); params.append(payload_json)
        if is_nsfw is not None:
            sets.append("is_nsfw = ?"); params.append(1 if is_nsfw else 0)
        if not sets:
            return False
        sets.append("updated_at = ?"); params.append(jst_now())
        params.append(preset_id)
        rowcount = await self.execute_returning_rowcount(
            f"UPDATE prompt_section_presets SET {', '.join(sets)} WHERE id = ?",
            tuple(params),
        )
        return rowcount == 1

    async def section_preset_delete(self, preset_id: int) -> bool:
        rowcount = await self.execute_returning_rowcount(
            "DELETE FROM prompt_section_presets WHERE id = ?", (preset_id,),
        )
        return rowcount == 1
