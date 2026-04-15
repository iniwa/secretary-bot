"""PromptCrafterUnit — LLM 会話でプロンプトを育成するユニット。

- Discord/WebGUI 双方向対応（platform カラムで識別）
- ユーザーの自然言語入力を LLM で positive/negative プロンプト（SDXL 想定）に変換
- 既存セッションがあれば「現状 + 指示」を LLM に渡して差分編集（完全再生成はしない）
- `prompt_sessions` テーブルに永続化（TTL 7日、cleanup は定期実行）
- `image_gen` から `get_active_prompt(user_id, platform)` で参照可能
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit

log = get_logger(__name__)

_DEFAULT_CLEANUP_INTERVAL_SEC = 3600  # 1時間おきに TTL 切れを掃除
_HISTORY_MAX_TURNS = 10               # history_json 保持上限（往復ペア）

_CRAFT_PROMPT = """\
あなたは SDXL 向け画像生成プロンプト編集アシスタント。
ユーザーの指示を解釈し、英語のプロンプトを JSON で返してください。

## 出力形式（厳守）
{{"positive": "...", "negative": "...", "note": "一行コメント日本語可"}}

- positive: カンマ区切りのタグ列（英語推奨、重複を避ける）
- negative: ネガティブプロンプト（空なら空文字）
- note: ユーザー向け短評（省略可）
- JSON 1 個のみ。他テキストは禁止。

## 現在の状態
positive: {current_positive}
negative: {current_negative}

## ユーザー指示
{user_input}

## ルール
- 現在の状態が非空なら差分編集（完全再生成はしない）。
- 「リセット」「作り直し」等の明示がある場合のみ全面書き換え。
- 危険/違法/ポリシー違反は note に日本語で理由を添えて拒否（positive/negative を空にする）。
"""

_ACTION_PROMPT = """\
あなたはプロンプト編集ユニットの意図抽出アシスタント。ユーザー入力を JSON で分類してください。

## アクション
- craft: プロンプトを作成/編集する（既定）
- show:  現在のセッション内容を表示
- clear: セッションを破棄

## 出力形式（厳守）
{{"action": "...", "instruction": "..."}}

- JSON 1 個のみ。

## ユーザー入力
{user_input}
"""


class PromptCrafterUnit(BaseUnit):
    UNIT_NAME = "prompt_crafter"
    UNIT_DESCRIPTION = "LLM 会話で画像生成プロンプトを作成・編集する。"
    DELEGATE_TO = None
    AUTONOMY_TIER = 4
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        cfg = (bot.config.get("units") or {}).get(self.UNIT_NAME) or {}
        self._cleanup_interval = int(
            cfg.get("cleanup_interval_seconds", _DEFAULT_CLEANUP_INTERVAL_SEC),
        )
        self._ttl_days = int(cfg.get("session_ttl_days", 7))
        self._cleanup_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="prompt_crafter_cleanup",
        )

    async def cog_unload(self) -> None:
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    # --- public API (他ユニットから参照) ---

    async def get_active_prompt(
        self, user_id: str, platform: str,
    ) -> dict | None:
        """image_gen 等から参照する。{'positive','negative','session_id'} を返す。"""
        row = await self.bot.database.prompt_session_get_active(
            user_id=user_id, platform=platform,
        )
        if not row:
            return None
        return {
            "session_id": int(row["id"]),
            "positive": row.get("positive") or "",
            "negative": row.get("negative") or "",
            "params_json": row.get("params_json") or "",
            "base_workflow_id": row.get("base_workflow_id"),
        }

    async def craft(
        self, *, user_id: str, platform: str, instruction: str,
        base_workflow_id: int | None = None,
    ) -> dict:
        """プロンプトを新規作成または差分編集する。結果を dict で返す。"""
        existing = await self.bot.database.prompt_session_get_active(
            user_id=user_id, platform=platform,
        )
        current_positive = (existing.get("positive") if existing else "") or ""
        current_negative = (existing.get("negative") if existing else "") or ""

        prompt = _CRAFT_PROMPT.format(
            current_positive=current_positive or "(empty)",
            current_negative=current_negative or "(empty)",
            user_input=instruction,
        )
        parsed: dict[str, Any] = await self.llm.extract_json(prompt)
        new_positive = (parsed.get("positive") or "").strip()
        new_negative = (parsed.get("negative") or "").strip()
        note = (parsed.get("note") or "").strip()

        history = self._load_history(existing)
        history.append({
            "instruction": instruction,
            "positive": new_positive,
            "negative": new_negative,
            "note": note,
        })
        if len(history) > _HISTORY_MAX_TURNS:
            history = history[-_HISTORY_MAX_TURNS:]
        history_json = json.dumps(history, ensure_ascii=False)

        if existing:
            session_id = int(existing["id"])
            await self.bot.database.prompt_session_update(
                session_id,
                positive=new_positive,
                negative=new_negative,
                history_json=history_json,
                base_workflow_id=base_workflow_id,
                ttl_days=self._ttl_days,
            )
        else:
            session_id = await self.bot.database.prompt_session_insert(
                user_id=user_id, platform=platform,
                positive=new_positive, negative=new_negative,
                history_json=history_json,
                base_workflow_id=base_workflow_id,
                ttl_days=self._ttl_days,
            )

        return {
            "session_id": session_id,
            "positive": new_positive,
            "negative": new_negative,
            "note": note,
        }

    async def clear_active(self, user_id: str, platform: str) -> bool:
        row = await self.bot.database.prompt_session_get_active(
            user_id=user_id, platform=platform,
        )
        if not row:
            return False
        await self.bot.database.prompt_session_delete(int(row["id"]))
        return True

    # --- Discord execute ---

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)

        message = (parsed.get("message") or "").strip()
        user_id = parsed.get("user_id") or ""
        platform = parsed.get("platform") or "discord"

        if not message:
            return "プロンプト指示が空だよ。何を作る？"

        try:
            action_info: dict = await self.llm.extract_json(
                _ACTION_PROMPT.format(user_input=message),
            )
            action = (action_info.get("action") or "craft").lower()
            instruction = (action_info.get("instruction") or message).strip()

            if action == "show":
                result = await self._discord_show(user_id, platform)
            elif action == "clear":
                result = await self._discord_clear(user_id, platform)
            else:
                result = await self._discord_craft(user_id, platform, instruction)
            self.breaker.record_success()
            return result
        except Exception as e:
            self.breaker.record_failure()
            log.error("prompt_crafter execute failed: %s", e, exc_info=True)
            await ft.emit("UNIT_EXEC", "error", {"error": str(e)}, flow_id)
            return f"プロンプト編集でエラーが起きたよ: {e}"
        finally:
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)

    async def _discord_craft(
        self, user_id: str, platform: str, instruction: str,
    ) -> str:
        result = await self.craft(
            user_id=user_id, platform=platform, instruction=instruction,
        )
        lines = [f"セッション #{result['session_id']} を更新したよ。"]
        if result.get("note"):
            lines.append(f"メモ: {result['note']}")
        lines.append(f"positive: `{result['positive'] or '(empty)'}`")
        if result.get("negative"):
            lines.append(f"negative: `{result['negative']}`")
        lines.append("→ このまま `/image generate` で使えるよ。")
        return "\n".join(lines)

    async def _discord_show(self, user_id: str, platform: str) -> str:
        prompt = await self.get_active_prompt(user_id, platform)
        if not prompt:
            return "有効なプロンプトセッションはないよ。"
        lines = [
            f"セッション #{prompt['session_id']}",
            f"positive: `{prompt['positive'] or '(empty)'}`",
        ]
        if prompt.get("negative"):
            lines.append(f"negative: `{prompt['negative']}`")
        return "\n".join(lines)

    async def _discord_clear(self, user_id: str, platform: str) -> str:
        ok = await self.clear_active(user_id, platform)
        return "セッションを破棄したよ。" if ok else "有効なセッションはなかったよ。"

    # --- helpers ---

    @staticmethod
    def _load_history(row: dict | None) -> list[dict]:
        if not row:
            return []
        raw = row.get("history_json") or ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    async def _cleanup_loop(self) -> None:
        await asyncio.sleep(self._cleanup_interval)
        while True:
            try:
                n = await self.bot.database.prompt_session_cleanup_expired()
                if n:
                    log.info("prompt_crafter cleanup: removed %d expired sessions", n)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("prompt_crafter cleanup failed: %s", e)
            try:
                await asyncio.sleep(self._cleanup_interval)
            except asyncio.CancelledError:
                raise


async def setup(bot) -> None:
    await bot.add_cog(PromptCrafterUnit(bot))
