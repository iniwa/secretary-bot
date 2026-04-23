"""PC・サーバー状態確認ユニット。

`execute()` はシステム状態を Discord に返す軽量応答。
`on_heartbeat()` ではグローバル IP 変動検知を担う（kobo_watch 等の楽天 API
Allow IP が変わったとき手動更新を促すため）。
"""

from __future__ import annotations

import time

import httpx

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit

log = get_logger(__name__)

_IP_STATE_KEY = "global_ip"
_IP_LAST_CHECK_KEY = "global_ip_last_check_ts"
_DEFAULT_IP_ENDPOINT = "https://api.ipify.org"


class StatusUnit(BaseUnit):
    UNIT_NAME = "status"
    UNIT_DESCRIPTION = "PCやサーバーの稼働状況を確認。「PCは起きてる？」「ステータス確認」など。"
    AUTONOMY_TIER = 0
    AUTONOMOUS_ACTIONS = ["get"]
    AUTONOMY_HINT = "get: params={}。システム状態を取得する軽量アクション。"

    def __init__(self, bot):
        super().__init__(bot)
        ip_cfg = ((bot.config.get("units") or {}).get(self.UNIT_NAME) or {}).get(
            "ip_watch", {},
        )
        self._ip_watch_enabled: bool = bool(ip_cfg.get("enabled", True))
        self._ip_watch_endpoint: str = str(
            ip_cfg.get("endpoint", _DEFAULT_IP_ENDPOINT),
        )
        self._ip_watch_interval_min: int = int(
            ip_cfg.get("check_interval_min", 30),
        )

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            status = await self.bot.status_collector.collect()
            result = self.bot.status_collector.format_discord(status)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    # === IP 変動検知（heartbeat 連携）===

    async def on_heartbeat(self) -> None:
        if not self._ip_watch_enabled:
            return
        # 前回チェックから interval_min 未満ならスキップ（heartbeat は 15 分毎だが
        # ユーザー設定でさらに間隔を空けたい場合のガード）。
        now = time.time()
        last_str = await self.bot.database.system_state_get(_IP_LAST_CHECK_KEY)
        if last_str:
            try:
                if now - float(last_str) < self._ip_watch_interval_min * 60:
                    return
            except ValueError:
                pass
        await self.bot.database.system_state_set(_IP_LAST_CHECK_KEY, str(now))

        current = await self._fetch_global_ip()
        if not current:
            return
        previous = await self.bot.database.system_state_get(_IP_STATE_KEY)
        if previous is None:
            await self.bot.database.system_state_set(_IP_STATE_KEY, current)
            log.info("ip_watch initial: %s", current)
            return
        if current != previous:
            await self.bot.database.system_state_set(_IP_STATE_KEY, current)
            await self._notify_ip_changed(previous, current)

    async def _fetch_global_ip(self) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(self._ip_watch_endpoint)
            if resp.status_code != 200:
                return None
            text = (resp.text or "").strip()
            return text or None
        except httpx.HTTPError as e:
            log.debug("ip_watch fetch failed: %s", e)
            return None

    async def _notify_ip_changed(self, previous: str, current: str) -> None:
        msg = (
            "⚠️ グローバル IP が変わったよ\n"
            f"前回: `{previous}`\n"
            f"今回: `{current}`\n\n"
            "楽天 API (kobo_watch) が動かなくなる前に、楽天管理画面で Allow IP を更新してね。\n"
            "https://webservice.rakuten.co.jp/app/list"
        )
        await self.notify(msg)


async def setup(bot) -> None:
    await bot.add_cog(StatusUnit(bot))
