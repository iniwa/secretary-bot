"""PC電源管理ユニット。起動（WoL）・シャットダウン・再起動。"""

import os
import time

import httpx

from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit
from src.logger import get_logger

log = get_logger(__name__)

_CONFIRM_YES = ("はい", "うん", "yes", "ok", "おk", "おけ", "お願い", "そう", "合ってる", "合ってます", "それで")
_CONFIRM_NO = ("いいえ", "いや", "no", "やめ", "キャンセル", "違う", "ちがう", "やめて", "違います")

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- wake: PCを起動する（WoL）
- shutdown: PCをシャットダウンする
- restart: PCを再起動する
- status: PCの状態を確認する
- cancel: 予約済みのシャットダウン/再起動をキャンセルする

## 対象PC一覧
{pc_list}

## 出力形式（厳守）
{{"action": "アクション名", "target": "PC識別子"}}

- target が不明な場合は省略してください（デフォルトで最優先のPCが使われます）。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""


class PowerUnit(BaseUnit):
    UNIT_NAME = "power"
    UNIT_DESCRIPTION = "PCの電源管理。起動（WoL）・シャットダウン・再起動。「メインPCを起動して」「PCをシャットダウン」など。"

    def __init__(self, bot):
        super().__init__(bot)
        self._agents = bot.config.get("windows_agents", [])
        self._agent_token = os.environ.get("AGENT_SECRET_TOKEN", "")

        wol_cfg = bot.config.get("wol", {})
        self._wol_url = wol_cfg.get("url", "http://localhost:8090")

        power_cfg = bot.config.get("units", {}).get("power", {})
        self._shutdown_delay = power_cfg.get("shutdown_delay", 60)

        # pc-id → WoLデバイスIDマッピング
        self._pc_to_wol_device: dict[str, str] = {}
        # pc-id → agent設定
        self._pc_map: dict[str, dict] = {}
        for agent in self._agents:
            aid = agent["id"]
            self._pc_map[aid] = agent
            wol_id = agent.get("wol_device_id", "")
            if wol_id:
                self._pc_to_wol_device[aid] = wol_id

        # 確認待ち保留アクション
        self._pending_actions: dict[str, dict] = {}
        # キャンセル可能期間の追跡
        self._active_shutdowns: dict[str, float] = {}

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        message = parsed.get("message", "")
        channel = parsed.get("channel", "")

        try:
            # 確認待ちの保留アクションがある場合
            if channel and channel in self._pending_actions:
                result = await self._handle_confirmation(channel, message)
                if result is not None:
                    result = await self.personalize(result, message, flow_id)
                    self.breaker.record_success()
                    await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "confirm"}, flow_id)
                    return result

            extracted = await self._extract_params(message, channel)
            action = extracted.get("action", "status")
            target = extracted.get("target") or self._default_target()

            if action == "wake":
                result = await self._wake_pc(target)
                self.session_done = True
            elif action in ("shutdown", "restart"):
                # 確認プロンプトを出す
                agent = self._pc_map.get(target)
                name = agent["name"] if agent else target
                action_label = "シャットダウン" if action == "shutdown" else "再起動"
                self._pending_actions[channel] = {
                    "action": action,
                    "target": target,
                }
                self.session_done = False
                result = f"{name}を{action_label}します。よろしいですか？"
            elif action == "cancel":
                result = await self._cancel_shutdown(target)
                self.session_done = True
            else:
                result = await self._status(target)
                self.session_done = True

            result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": action}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        pc_lines = []
        for agent in self._agents:
            pc_lines.append(f"- {agent['id']}: {agent['name']}")
        pc_list = "\n".join(pc_lines) if pc_lines else "- （登録なし）"

        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(
            pc_list=pc_list,
            user_input=user_input,
        )
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    def _default_target(self) -> str:
        if self._agents:
            return self._agents[0]["id"]
        return ""

    # --- 確認フロー ---

    def _check_confirmation(self, message: str) -> bool | None:
        msg = message.strip()
        if len(msg) > 30:
            return None
        msg_lower = msg.lower()
        if any(w in msg_lower for w in _CONFIRM_NO):
            return False
        if any(w in msg_lower for w in _CONFIRM_YES):
            return True
        return None

    async def _handle_confirmation(self, channel: str, message: str) -> str | None:
        pending = self._pending_actions.pop(channel)
        confirmed = self._check_confirmation(message)

        if confirmed is None:
            return None

        if not confirmed:
            self.session_done = True
            return "キャンセルしました。"

        action = pending["action"]
        target = pending["target"]

        if action == "shutdown":
            result = await self._shutdown_pc(target)
        else:
            result = await self._restart_pc(target)

        self.session_done = True
        return result

    # --- アクション実装 ---

    async def _wake_pc(self, target: str) -> str:
        agent = self._pc_map.get(target)
        if not agent:
            return f"PC「{target}」が見つかりません。"
        name = agent["name"]

        # プリフライト: 既に起動しているか確認
        if await self._is_agent_alive(agent):
            return f"{name}は既に起動しています。"

        wol_device_id = self._pc_to_wol_device.get(target)
        if not wol_device_id:
            return f"{name}のWoLデバイスIDが設定されていません。config.yamlのwol_device_idを確認してください。"

        url = f"{self._wol_url}/api/devices/{wol_device_id}/wake"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error("WoL API error for %s: %s", target, e)
            return f"{name}へのWoLパケット送信に失敗しました。WoLツールの状態を確認してください。"

        return f"{name}にWoLパケットを送信しました。起動まで1〜2分かかります。"

    async def _shutdown_pc(self, target: str) -> str:
        agent = self._pc_map.get(target)
        if not agent:
            return f"PC「{target}」が見つかりません。"
        name = agent["name"]

        if not await self._is_agent_alive(agent):
            return f"{name}に接続できません。既にオフラインの可能性があります。"

        url = f"http://{agent['host']}:{agent['port']}/shutdown"
        delay = self._shutdown_delay
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    json={"delay": delay},
                    headers={"X-Agent-Token": self._agent_token},
                )
                resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Shutdown API error for %s: %s", target, e)
            return f"{name}のシャットダウン要求に失敗しました。"

        self._active_shutdowns[target] = time.monotonic()
        return f"{name}を{delay}秒後にシャットダウンします。キャンセルする場合は「キャンセル」と言ってください。"

    async def _restart_pc(self, target: str) -> str:
        agent = self._pc_map.get(target)
        if not agent:
            return f"PC「{target}」が見つかりません。"
        name = agent["name"]

        if not await self._is_agent_alive(agent):
            return f"{name}に接続できません。既にオフラインの可能性があります。"

        url = f"http://{agent['host']}:{agent['port']}/restart"
        delay = self._shutdown_delay
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    json={"delay": delay},
                    headers={"X-Agent-Token": self._agent_token},
                )
                resp.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Restart API error for %s: %s", target, e)
            return f"{name}の再起動要求に失敗しました。"

        self._active_shutdowns[target] = time.monotonic()
        return f"{name}を{delay}秒後に再起動します。キャンセルする場合は「キャンセル」と言ってください。"

    async def _cancel_shutdown(self, target: str) -> str:
        agent = self._pc_map.get(target)
        if not agent:
            return f"PC「{target}」が見つかりません。"
        name = agent["name"]

        if not await self._is_agent_alive(agent):
            return f"{name}に接続できません。"

        url = f"http://{agent['host']}:{agent['port']}/cancel-shutdown"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    headers={"X-Agent-Token": self._agent_token},
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            log.error("Cancel shutdown API error for %s: %s", target, e)
            return f"{name}のキャンセル要求に失敗しました。"

        self._active_shutdowns.pop(target, None)
        if data.get("status") == "cancelled":
            return f"{name}のシャットダウン/再起動をキャンセルしました。"
        return f"{name}に予約中のシャットダウン/再起動はありません。"

    async def _status(self, target: str) -> str:
        if not target:
            # 全PC一覧
            lines = ["🖥️ PC状態一覧", "━━━━━━━━━━━━━━━━━━━━"]
            for agent in self._agents:
                alive = await self._is_agent_alive(agent)
                icon = "🟢" if alive else "🔴"
                lines.append(f"  {icon} {agent['name']} ({agent['id']})")
            return "\n".join(lines)

        agent = self._pc_map.get(target)
        if not agent:
            return f"PC「{target}」が見つかりません。"
        alive = await self._is_agent_alive(agent)
        icon = "🟢 オンライン" if alive else "🔴 オフライン"
        return f"{agent['name']}: {icon}"

    # --- ヘルパー ---

    async def _is_agent_alive(self, agent: dict) -> bool:
        url = f"http://{agent['host']}:{agent['port']}/health"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers={"X-Agent-Token": self._agent_token})
                return resp.status_code == 200
        except Exception:
            return False


async def setup(bot) -> None:
    await bot.add_cog(PowerUnit(bot))
