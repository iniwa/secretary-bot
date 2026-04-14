"""Actuator — decision を Tier で判定し、実行 or 承認待ちに振り分ける。"""

import json
from datetime import datetime, timedelta

import discord

from src.database import JST, jst_now
from src.inner_mind.approval_view import ApprovalView
from src.logger import get_logger

log = get_logger(__name__)


class Actuator:
    """decision を受け取り、Tierに応じて実行 or pending_actions 登録を行う。"""

    def __init__(self, bot):
        self.bot = bot

    # --- 設定取得ヘルパー ---

    async def _get_setting(self, key: str, default: str = "") -> str:
        val = await self.bot.database.get_setting(key)
        return val if val is not None else default

    async def _mode(self) -> str:
        return (await self._get_setting("inner_mind.autonomy.mode", "off")).strip()

    async def _approval_timeout_minutes(self) -> int:
        try:
            return int(await self._get_setting("inner_mind.autonomy.approval_timeout_minutes", "30"))
        except (TypeError, ValueError):
            return 30

    async def _concurrent_pending(self) -> str:
        return (await self._get_setting("inner_mind.autonomy.concurrent_pending", "queue")).strip()

    async def _daily_limit(self, tier: int) -> int:
        key = f"inner_mind.autonomy.t{tier}_daily_limit"
        try:
            return int(await self._get_setting(key, "0"))
        except (TypeError, ValueError):
            return 0

    async def _allowed_units(self, tier: int) -> set[str]:
        key = f"inner_mind.autonomy.t{tier}_allowed_units"
        raw = await self._get_setting(key, "")
        return {x.strip() for x in raw.split(",") if x.strip()}

    async def _target_user_id(self) -> str:
        return await self._get_setting("inner_mind.target_user_id", "")

    async def _speak_channel_id(self) -> str:
        return await self._get_setting("inner_mind.speak_channel_id", "")

    async def _show_reasoning(self) -> bool:
        return (await self._get_setting("inner_mind.autonomy.show_reasoning", "true")).lower() == "true"

    # --- tier 判定 ---

    def _resolve_unit_tier(self, unit_name: str, method: str) -> tuple[int, bool]:
        """(tier, is_action_exposed)。exposed=False なら呼び出し不可。"""
        cog = self.bot.get_cog(unit_name) if hasattr(self.bot, "get_cog") else None
        if cog is None:
            return 4, False
        tier = int(getattr(cog, "AUTONOMY_TIER", 4))
        actions = getattr(cog, "AUTONOMOUS_ACTIONS", []) or []
        return tier, (method in actions)

    # --- メイン API ---

    async def dispatch(self, decision: dict, monologue_id: int | None = None) -> dict:
        """decision を実行。

        decision = {
            "action": "memo.add" | "speak" | "ask_user" | "no_op" | "call_unit" ...,
            "unit": "memo",  # optional
            "method": "add",
            "params": {...},
            "reasoning": "...",
            "summary": "...",  # ユーザー提示用要約
        }

        return: {"status": "executed"|"queued"|"skipped"|"failed", "pending_id": int|None, ...}
        """
        mode = await self._mode()
        if mode == "off":
            return {"status": "skipped", "reason": "autonomy_off"}

        action = decision.get("action") or ""
        unit_name = decision.get("unit") or ""
        method = decision.get("method") or ""

        # ── 内部アクション (T0/T1) は直接実行 ──
        if action in ("no_op",):
            return {"status": "skipped", "reason": "no_op"}
        if action == "speak":
            return await self._execute_speak(decision)
        if action in ("memorize", "update_self_model", "recall"):
            # observe_only でも OK
            return await self._execute_internal(action, decision)

        # ── ユニット呼び出し (T2/T3) ──
        if not unit_name or not method:
            return {"status": "failed", "reason": "missing_unit_or_method"}

        tier, exposed = self._resolve_unit_tier(unit_name, method)
        if not exposed:
            return {"status": "skipped", "reason": f"action_not_exposed:{unit_name}.{method}"}

        if tier <= 1:
            # T0/T1 は直接実行
            return await self._execute_unit(unit_name, method, decision, monologue_id)

        # T2/T3 の処理
        if mode == "observe_only":
            return {"status": "skipped", "reason": "observe_only"}

        # 許可リスト
        allowed = await self._allowed_units(tier)
        if allowed:
            key = f"{unit_name}.{method}"
            if key not in allowed:
                return {"status": "skipped", "reason": f"not_allowed:{key}"}

        # 日次上限
        user_id = await self._target_user_id()
        limit = await self._daily_limit(tier)
        if limit > 0:
            used = await self.bot.database.count_pending_today(tier, user_id)
            if used >= limit:
                return {"status": "skipped", "reason": f"daily_limit_reached:t{tier}"}

        # mode=full の場合は承認スキップして即実行
        if mode == "full":
            return await self._execute_unit(unit_name, method, decision, monologue_id)

        # proposal モード → 承認待ちへ
        return await self._enqueue_pending(
            monologue_id=monologue_id,
            tier=tier,
            unit_name=unit_name,
            method=method,
            decision=decision,
            user_id=user_id,
        )

    # --- 実行系 ---

    async def _execute_speak(self, decision: dict) -> dict:
        text = decision.get("params", {}).get("text") or decision.get("text") or ""
        if not text:
            return {"status": "failed", "reason": "empty_speak"}
        channel_id = await self._speak_channel_id()
        if not channel_id:
            return {"status": "failed", "reason": "no_channel"}
        try:
            ch = self.bot.get_channel(int(channel_id))
        except (TypeError, ValueError):
            ch = None
        if ch is None:
            return {"status": "failed", "reason": "channel_not_found"}
        await ch.send(text)
        return {"status": "executed", "result": {"sent": True}}

    async def _execute_internal(self, action: str, decision: dict) -> dict:
        """T0 の内部処理。現時点では InnerMind 側に委譲（存在すれば）。"""
        inner = getattr(self.bot, "inner_mind", None)
        if inner is None:
            return {"status": "failed", "reason": "no_inner_mind"}
        handler = getattr(inner, f"do_{action}", None)
        if handler is None:
            return {"status": "skipped", "reason": f"no_handler:{action}"}
        try:
            result = await handler(decision.get("params") or {})
            return {"status": "executed", "result": result}
        except Exception as e:
            log.warning("Internal action %s failed: %s", action, e)
            return {"status": "failed", "reason": str(e)}

    async def _execute_unit(
        self, unit_name: str, method: str, decision: dict, monologue_id: int | None,
    ) -> dict:
        """ユニットの autonomous_execute を呼び出す。"""
        cog = self.bot.get_cog(unit_name)
        if cog is None:
            return {"status": "failed", "reason": f"unit_not_found:{unit_name}"}
        fn = getattr(cog, "autonomous_execute", None)
        if fn is None:
            return {"status": "failed", "reason": "no_autonomous_execute"}
        user_id = await self._target_user_id()
        params = decision.get("params") or {}
        try:
            result = await fn(method, params, user_id)
            if monologue_id:
                await self.bot.database.set_monologue_action_result(
                    monologue_id, json.dumps(result, ensure_ascii=False),
                )
            return {"status": "executed", "result": result}
        except Exception as e:
            log.warning("Autonomous exec %s.%s failed: %s", unit_name, method, e)
            return {"status": "failed", "reason": str(e)}

    # --- 承認待ち ---

    async def _enqueue_pending(
        self,
        *,
        monologue_id: int | None,
        tier: int,
        unit_name: str,
        method: str,
        decision: dict,
        user_id: str,
    ) -> dict:
        # 同時 pending 制御
        concurrent = await self._concurrent_pending()
        existing = await self.bot.database.list_pending_actions(status="pending", limit=50)
        existing_self = [p for p in existing if p.get("user_id") == user_id]
        if existing_self:
            if concurrent == "single":
                return {"status": "skipped", "reason": "has_pending:single"}
            if concurrent == "prefer_new":
                for p in existing_self:
                    await self.bot.database.resolve_pending_action(
                        p["id"], "cancelled_by_newer", None, None,
                    )
                    await self._rewrite_approval_message(p, "❌ 新しい提案で置き換え")

        now = jst_now()
        timeout_min = await self._approval_timeout_minutes()
        expires_at = now + timedelta(minutes=timeout_min)
        channel_id = await self._speak_channel_id()

        summary = decision.get("summary") or f"{unit_name}.{method} を実行しようとしています"
        reasoning = decision.get("reasoning") or ""
        params_json = json.dumps(decision.get("params") or {}, ensure_ascii=False)

        pending_id = await self.bot.database.create_pending_action(
            monologue_id=monologue_id,
            tier=tier,
            unit_name=unit_name,
            method=method,
            params=params_json,
            reasoning=reasoning,
            summary=summary,
            user_id=user_id,
            channel_id=channel_id or None,
            expires_at=expires_at.isoformat(),
        )

        # Discord に送信
        msg_id = await self._send_approval_message(
            channel_id, pending_id, summary, reasoning, tier, expires_at, user_id,
        )
        if msg_id:
            await self.bot.database.set_pending_discord_message(pending_id, str(msg_id))

        return {"status": "queued", "pending_id": pending_id}

    async def _send_approval_message(
        self,
        channel_id: str,
        pending_id: int,
        summary: str,
        reasoning: str,
        tier: int,
        expires_at: datetime,
        user_id: str,
    ) -> int | None:
        if not channel_id:
            return None
        try:
            ch = self.bot.get_channel(int(channel_id))
        except (TypeError, ValueError):
            ch = None
        if ch is None:
            log.warning("Approval channel not found: %s", channel_id)
            return None

        show_reasoning = await self._show_reasoning()
        embed = discord.Embed(
            title="【ミミからの提案】",
            description=summary,
            color=0x4A90E2,
        )
        if show_reasoning and reasoning:
            embed.add_field(name="理由", value=reasoning, inline=False)
        embed.add_field(
            name="期限",
            value=expires_at.strftime("%Y-%m-%d %H:%M JST"),
            inline=False,
        )
        embed.set_footer(text=f"Tier {tier} / pending #{pending_id}")

        content = f"<@{user_id}>" if user_id else None
        view = ApprovalView(pending_id=pending_id)
        try:
            msg = await ch.send(content=content, embed=embed, view=view)
            return msg.id
        except Exception as e:
            log.warning("Failed to send approval message: %s", e)
            return None

    async def _rewrite_approval_message(self, pending: dict, prefix: str) -> None:
        """既存の承認メッセージを無効化表示に書き換える。"""
        msg_id = pending.get("discord_message_id")
        channel_id = pending.get("channel_id")
        if not msg_id or not channel_id:
            return
        try:
            ch = self.bot.get_channel(int(channel_id))
            if ch is None:
                return
            msg = await ch.fetch_message(int(msg_id))
            embed = msg.embeds[0] if msg.embeds else discord.Embed()
            embed.title = f"{prefix}: {embed.title or ''}"
            await msg.edit(embed=embed, view=None)
        except Exception as e:
            log.debug("Rewrite approval msg failed: %s", e)

    # --- Interaction ハンドリング ---

    async def handle_interaction(
        self, interaction: discord.Interaction, pending_id: int, decision: str,
    ) -> None:
        pending = await self.bot.database.get_pending_action(pending_id)
        if pending is None:
            await interaction.response.send_message("該当 pending が見つかりません", ephemeral=True)
            return
        if pending.get("status") != "pending":
            await interaction.response.send_message(
                f"この提案は既に {pending.get('status')} です", ephemeral=True,
            )
            return
        # ユーザー検証
        expected_user = pending.get("user_id")
        if expected_user and str(interaction.user.id) != str(expected_user):
            await interaction.response.send_message("対象ユーザーのみ操作できます", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)

        if decision == "ng":
            await self.bot.database.resolve_pending_action(
                pending_id, "rejected", None, None,
            )
            await self._edit_to_final(interaction, pending, "❌ 却下", None)
            return

        # OK: 実行
        try:
            params = json.loads(pending.get("params") or "{}")
        except Exception:
            params = {}
        unit_name = pending.get("unit_name") or ""
        method = pending.get("method") or ""
        result = await self._execute_unit(
            unit_name, method,
            {"params": params},
            pending.get("monologue_id"),
        )
        if result.get("status") == "executed":
            await self.bot.database.resolve_pending_action(
                pending_id, "executed",
                json.dumps(result.get("result"), ensure_ascii=False), None,
            )
            await self._edit_to_final(interaction, pending, "✅ 実行済み", result.get("result"))
        else:
            await self.bot.database.resolve_pending_action(
                pending_id, "failed", None, result.get("reason"),
            )
            await self._edit_to_final(interaction, pending, "⚠️ 実行失敗", result)

    async def _edit_to_final(
        self, interaction: discord.Interaction, pending: dict,
        label: str, result: dict | None,
    ) -> None:
        try:
            msg = interaction.message
            if msg is None:
                return
            embed = msg.embeds[0] if msg.embeds else discord.Embed()
            embed.title = f"{label} / {embed.title or ''}"
            if result is not None:
                text = json.dumps(result, ensure_ascii=False)[:500]
                embed.add_field(name="結果", value=text or "(空)", inline=False)
            await msg.edit(embed=embed, view=None)
        except Exception as e:
            log.debug("Edit final msg failed: %s", e)

    # --- タイムアウト一括処理 ---

    async def expire_overdue(self) -> int:
        """expires_at を過ぎた pending を expired に。戻り値=処理件数。"""
        now = jst_now()
        pendings = await self.bot.database.list_pending_actions(status="pending", limit=200)
        count = 0
        for p in pendings:
            exp = p.get("expires_at")
            if not exp:
                continue
            try:
                exp_dt = exp if isinstance(exp, datetime) else datetime.fromisoformat(str(exp))
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=JST)
            except Exception:
                continue
            if exp_dt <= now:
                await self.bot.database.resolve_pending_action(
                    p["id"], "expired", None, None,
                )
                await self._rewrite_approval_message(p, "⌛ 期限切れ")
                count += 1
        return count
