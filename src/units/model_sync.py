"""ModelSyncUnit — 画像生成 Agent のモデルキャッシュ状態を定期ポーリング。

- 一定間隔で `/capability` を取得
- `model_cache_manifest` を同期
- `units.image_gen.default_base_model` が未キャッシュなら `/cache/sync` を発射

Dispatcher の起動時ウォームアップと同じロジック（`image_gen.warmup`）を
再利用する。Discord 連携は持たない純粋なバックグラウンドユニット。
"""

from __future__ import annotations

import asyncio

from src.logger import get_logger
from src.units.base_unit import BaseUnit
from src.units.image_gen.warmup import warmup_all_agents

log = get_logger(__name__)

_DEFAULT_INTERVAL_SEC = 1800  # 30分
_INITIAL_DELAY_SEC = 30       # bot 起動直後のバーストを避けるための初回遅延


class ModelSyncUnit(BaseUnit):
    UNIT_NAME = "model_sync"
    UNIT_DESCRIPTION = "画像生成 Agent のモデルキャッシュを定期ポーリングして DB へ同期する。"
    DELEGATE_TO = None
    CHAT_ROUTABLE = False
    AUTONOMY_TIER = 4
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        cfg = (bot.config.get("units") or {}).get(self.UNIT_NAME) or {}
        self._interval = int(cfg.get("interval_seconds", _DEFAULT_INTERVAL_SEC))
        self._trigger_sync = bool(cfg.get("trigger_sync", True))
        self._task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="model_sync_loop")
        log.info("model_sync started (interval=%ds, trigger_sync=%s)",
                 self._interval, self._trigger_sync)

    async def cog_unload(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("model_sync stopped")

    async def execute(self, ctx, parsed: dict) -> str | None:
        """Discord 経由で「model_sync 手動実行」を受け付ける簡易 execute。"""
        results = await warmup_all_agents(
            self.bot, trigger_sync=self._trigger_sync,
        )
        lines = [f"manifest sync: {len(results)} agent(s)"]
        for r in results:
            aid = r.get("agent_id") or "?"
            if r.get("error"):
                lines.append(f"- {aid}: error={r['error']}")
                continue
            cap = r.get("capability") or {}
            cnt = len(cap.get("models", []))
            note = ""
            if r.get("sync_id"):
                note = f" (sync_id={r['sync_id']})"
            elif r.get("missing_default"):
                note = " (missing default, sync skipped)"
            lines.append(f"- {aid}: checkpoints={cnt}{note}")
        return "\n".join(lines)

    async def _loop(self) -> None:
        await asyncio.sleep(_INITIAL_DELAY_SEC)
        while True:
            try:
                await warmup_all_agents(
                    self.bot, trigger_sync=self._trigger_sync,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("model_sync loop iteration failed: %s", e)
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise


async def setup(bot) -> None:
    await bot.add_cog(ModelSyncUnit(bot))
