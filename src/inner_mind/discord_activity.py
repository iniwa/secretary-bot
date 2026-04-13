"""DiscordActivityMonitor — ユーザーのDiscordステータス・アクティビティから
InnerMind の動作モードを決定する。

モード:
- "stop":         完全停止（配信中などリソース競合が最大）
- "collect_only": ContextSource の背景更新のみ許可。think() はスキップ
- "full":         フル思考サイクル（Ollamaが空いている状態）

判定順:
1. 配信中 (discord.Streaming)        → stop
2. ゲーム中 (discord.Game)           → collect_only
3. 直近N分にユーザー発言あり         → collect_only
4. それ以外                          → full

Spotify再生中・カスタムステータスのみは "full" に影響しない（情報としては注入する）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

try:  # 実行時のみ import（テスト用にフォールバック）
    import discord  # type: ignore
except Exception:  # pragma: no cover
    discord = None  # type: ignore

from src.database import JST
from src.logger import get_logger

log = get_logger(__name__)


class DiscordActivityMonitor:
    """discord.py の member.activities から InnerMind 動作モードを決定。"""

    def __init__(self, bot):
        self.bot = bot

    async def _get_target_user_id(self) -> int | None:
        im_cfg = self.bot.config.get("inner_mind", {})
        raw = await self.bot.database.get_setting("inner_mind.target_user_id")
        if raw is None:
            raw = im_cfg.get("target_user_id", "")
        try:
            uid = int(raw)
            return uid or None
        except (TypeError, ValueError):
            return None

    def _find_member(self, user_id: int):
        for guild in self.bot.guilds:
            member = guild.get_member(user_id)
            if member is not None:
                return member
        return None

    @staticmethod
    def _classify_activity(act: Any) -> tuple[str, str]:
        """activity を (種別, 表示名) にマップ。種別 ∈ {stream, game, spotify, custom, other}"""
        if discord is None:
            return "other", str(act)
        if isinstance(act, discord.Streaming):
            name = getattr(act, "name", "") or getattr(act, "platform", "") or "配信"
            return "stream", name
        if isinstance(act, discord.Spotify):
            title = getattr(act, "title", "") or "Spotify"
            artist = getattr(act, "artist", "")
            label = f"{title} / {artist}" if artist else title
            return "spotify", label
        if isinstance(act, discord.CustomActivity):
            label = getattr(act, "name", "") or ""
            return "custom", label
        if isinstance(act, discord.Game):
            return "game", getattr(act, "name", "") or "ゲーム"
        atype = getattr(act, "type", None)
        # discord.ActivityType.playing は Activity（BaseActivity）の場合もある
        if atype is not None and discord is not None:
            try:
                if atype == discord.ActivityType.playing:
                    return "game", getattr(act, "name", "") or "ゲーム"
                if atype == discord.ActivityType.streaming:
                    return "stream", getattr(act, "name", "") or "配信"
                if atype == discord.ActivityType.listening:
                    return "spotify", getattr(act, "name", "") or "Listening"
            except Exception:
                pass
        return "other", getattr(act, "name", "") or ""

    async def _has_recent_user_speech(self, threshold_minutes: int) -> bool:
        if threshold_minutes <= 0:
            return False
        cutoff = (datetime.now(JST) - timedelta(minutes=threshold_minutes)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = await self.bot.database.fetchall(
            "SELECT 1 FROM conversation_log "
            "WHERE role = 'user' AND timestamp >= ? LIMIT 1",
            (cutoff,),
        )
        return bool(rows)

    async def get_state(self) -> dict:
        """現在のモードとアクティビティ詳細を返す。

        返り値の形:
          {
            "mode": "stop" | "collect_only" | "full",
            "reason": str,
            "status": "online" | "idle" | "dnd" | "offline" | "unknown",
            "activities": [{"type": str, "name": str}, ...],
          }
        """
        user_id = await self._get_target_user_id()
        if user_id is None or discord is None:
            return {"mode": "full", "reason": "no_target", "status": "unknown", "activities": []}

        member = self._find_member(user_id)
        if member is None:
            # オフライン扱い＝フル思考OK（Ollamaが空いている）
            return {"mode": "full", "reason": "member_not_found", "status": "offline", "activities": []}

        status = str(getattr(member, "status", "unknown"))
        activities_raw = list(getattr(member, "activities", []) or [])
        classified = [self._classify_activity(a) for a in activities_raw]
        activities_info = [{"type": t, "name": n} for t, n in classified if n or t != "other"]

        # 1. 配信中 → stop
        if any(t == "stream" for t, _ in classified):
            return {
                "mode": "stop", "reason": "streaming",
                "status": status, "activities": activities_info,
            }

        # 2. ゲーム中 → collect_only
        if any(t == "game" for t, _ in classified):
            return {
                "mode": "collect_only", "reason": "gaming",
                "status": status, "activities": activities_info,
            }

        # 3. 直近発言あり → collect_only
        im_cfg = self.bot.config.get("inner_mind", {})
        threshold = int(im_cfg.get("active_threshold_minutes", 10))
        if await self._has_recent_user_speech(threshold):
            return {
                "mode": "collect_only", "reason": "recent_user_speech",
                "status": status, "activities": activities_info,
            }

        return {
            "mode": "full", "reason": "idle",
            "status": status, "activities": activities_info,
        }

    @staticmethod
    def format_activities(activities: list[dict]) -> str:
        """アクティビティ一覧をプロンプト用テキストに整形。空なら空文字。"""
        if not activities:
            return ""
        label_map = {
            "stream": "配信",
            "game": "ゲーム",
            "spotify": "Spotify",
            "custom": "カスタム",
            "other": "その他",
        }
        parts = []
        for a in activities:
            lbl = label_map.get(a.get("type", ""), a.get("type", ""))
            name = a.get("name", "")
            if name:
                parts.append(f"{lbl}: {name}")
            else:
                parts.append(lbl)
        return " / ".join(parts)
