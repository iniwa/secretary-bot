"""PC電源管理ユニット。起動（WoL）・シャットダウン・再起動。"""

import asyncio
import os
import time

import httpx

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit

log = get_logger(__name__)

_CONFIRM_YES = ("はい", "うん", "yes", "ok", "おk", "おけ", "お願い", "そう", "合ってる", "合ってます", "それで", "いいよ", "いいです", "いい", "ええ", "オッケー", "頼む", "頼みます", "よろしく")
_CONFIRM_NO = ("いいえ", "いや", "no", "やめ", "キャンセル", "違う", "ちがう", "やめて", "違います", "だめ", "ダメ", "やっぱ", "やっぱり", "止め")

# 複数対象に同じアクションを連続送出するときのクールタイム（秒）。
# WoL パケットや shutdown リクエストが密に飛ぶとパケットロスや
# ルータのマルチキャスト抑制に引っかかるため、余裕を持って 5 秒あける。
_MULTI_TARGET_COOLDOWN_SEC = 5.0

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
{{"action": "アクション名", "targets": ["PC識別子", ...]}}

- 1 台のみの場合でも targets は配列にしてください（要素 1）。
- 「両方」「全部」「両方のPC」のように複数を指す表現は targets に該当PC全てを入れてください。
- targets が特定できない / 不要な場合は空配列 [] を返してください（デフォルトで最優先のPCが使われます）。
- 後方互換として `target` (単数文字列) も受理しますが、基本は targets (配列) を使ってください。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""


class PowerUnit(BaseUnit):
    UNIT_NAME = "power"
    UNIT_DESCRIPTION = "PCの電源管理。起動（WoL）・シャットダウン・再起動。「メインPCを起動して」「PCをシャットダウン」など。"
    AUTONOMY_TIER = 3
    AUTONOMOUS_ACTIONS = ["sleep", "shutdown"]
    AUTONOMY_HINT = "sleep/shutdown: params={\"target\":\"main\"|\"sub\"}。深夜帯でPC稼働中かつユーザーoffline時のみ提案。破壊的なので慎重に。"

    ADMIN_ONLY = True

    def __init__(self, bot):
        super().__init__(bot)
        self._agents = bot.config.get("windows_agents", [])
        self._agent_token = os.environ.get("AGENT_SECRET_TOKEN", "")
        self._admin_user_id = os.environ.get("WEBGUI_USER_ID", "")

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

    def _is_admin(self, user_id: str) -> bool:
        if not self._admin_user_id:
            return False
        return str(user_id) == self._admin_user_id

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        # 管理者のみ使用可能
        user_id = parsed.get("user_id", "")
        if not self._is_admin(user_id):
            return "この機能は管理者のみ使用できます。"

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
            targets = self._resolve_targets(extracted, action)

            if action == "wake":
                result = await self._wake_pcs(targets)
                self.session_done = True
            elif action in ("shutdown", "restart"):
                # 確認プロンプトを出す
                names = [self._name_of(t) for t in targets]
                action_label = "シャットダウン" if action == "shutdown" else "再起動"
                self._pending_actions[channel] = {
                    "action": action,
                    "targets": list(targets),
                }
                self.session_done = False
                joined = "・".join(names) if names else "(対象なし)"
                if len(targets) > 1:
                    result = f"{joined}を順番に{action_label}します（各{int(_MULTI_TARGET_COOLDOWN_SEC)}秒間隔）。よろしいですか？"
                else:
                    result = f"{joined}を{action_label}します。よろしいですか？"
            elif action == "cancel":
                result = await self._cancel_shutdowns(targets)
                self.session_done = True
            else:
                # status: targets が 1 件でも複数でも _status に任せる（空なら全件）
                if len(targets) == 1:
                    result = await self._status(targets[0])
                else:
                    result = await self._status_multi(targets)
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

    def _resolve_targets(self, extracted: dict, action: str) -> list[str]:
        """LLM 抽出結果から有効な PC ID リストを構築する。

        - `targets` (配列) を優先、なければ `target` (単数) を 1 要素配列に。
        - status アクションで対象未指定のときは空リストを返し、`_status_multi`
          / `_status` 側が全件扱いにする（既存挙動と整合）。
        - wake/shutdown/restart/cancel で対象未指定のときはデフォルト（最優先 PC）
          1 件を返す（既存挙動と整合）。
        - 未知の PC ID や重複は除去する。
        """
        raw: list[str] = []
        val = extracted.get("targets")
        if isinstance(val, list):
            raw.extend(str(v) for v in val if v)
        single = extracted.get("target")
        if single and str(single) not in raw:
            raw.append(str(single))

        known = {a["id"] for a in self._agents}
        seen: set[str] = set()
        resolved: list[str] = []
        for t in raw:
            if t in known and t not in seen:
                seen.add(t)
                resolved.append(t)

        if resolved:
            return resolved
        if action == "status":
            return []  # 全件表示
        default = self._default_target()
        return [default] if default else []

    def _name_of(self, target: str) -> str:
        agent = self._pc_map.get(target)
        return agent["name"] if agent else target

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
        # 互換: 旧 pending に target (単数) が入っていた場合もケア
        targets = list(pending.get("targets") or [])
        if not targets and pending.get("target"):
            targets = [pending["target"]]

        runner = self._shutdown_pc if action == "shutdown" else self._restart_pc
        results = await self._run_sequential(runner, targets)

        self.session_done = True
        return "\n".join(results)

    # --- アクション実装（複数対象ラッパー） ---

    async def _run_sequential(self, runner, targets: list[str]) -> list[str]:
        """対象 PC ごとに runner を順次呼び出し、各結果文字列を返す。

        対象が 2 件以上のときは各呼び出しの間に ``_MULTI_TARGET_COOLDOWN_SEC``
        秒のクールタイムを挟む（WoL パケットのバースト送信や同時 shutdown で
        起きうる副作用を避けるため）。
        """
        results: list[str] = []
        for i, target in enumerate(targets):
            if i > 0:
                await asyncio.sleep(_MULTI_TARGET_COOLDOWN_SEC)
            try:
                results.append(await runner(target))
            except Exception as e:
                log.error("power unit runner failed for %s: %s", target, e)
                results.append(f"{self._name_of(target)}: エラー ({e})")
        return results

    async def _wake_pcs(self, targets: list[str]) -> str:
        if not targets:
            return "対象PCが見つかりません。"
        results = await self._run_sequential(self._wake_pc, targets)
        return "\n".join(results)

    async def _cancel_shutdowns(self, targets: list[str]) -> str:
        if not targets:
            return "対象PCが見つかりません。"
        results = await self._run_sequential(self._cancel_shutdown, targets)
        return "\n".join(results)

    async def _status_multi(self, targets: list[str]) -> str:
        """対象 PC リストの状態を 1 メッセージにまとめる。targets が空なら全件。"""
        agents: list[dict]
        if targets:
            agents = [self._pc_map[t] for t in targets if t in self._pc_map]
        else:
            agents = list(self._agents)
        if not agents:
            return "対象PCが見つかりません。"
        lines = ["🖥️ PC状態一覧", "━━━━━━━━━━━━━━━━━━━━"]
        for agent in agents:
            alive = await self._is_agent_alive(agent)
            icon = "🟢" if alive else "🔴"
            lines.append(f"  {icon} {agent['name']} ({agent['id']})")
        return "\n".join(lines)

    # --- アクション実装（単一対象） ---

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
        # shutdown が reboot にすり替わる事故（Windows Update + veto）を検知するため
        # バックグラウンドで結果確認する。cancel された場合は _active_shutdowns から外れている。
        asyncio.create_task(self._verify_shutdown(target, delay))
        return f"{name}を{delay}秒後にシャットダウンします。キャンセルする場合は「キャンセル」と言ってください。"

    async def _verify_shutdown(self, target: str, delay: int) -> None:
        """shutdown 指示後、実際に停止したかを確認する。
        Step1: delay+90s 後に /health が応答しないこと（shutdown 成功）
        Step2: さらに 300s 後も /health が応答しないこと（reboot にすり替わっていない）
        どちらの段階で異常を検知しても `notify_error` で通知する。
        """
        agent = self._pc_map.get(target)
        if not agent:
            return
        name = agent["name"]

        await asyncio.sleep(delay + 90)
        # ユーザがキャンセルしていたら検証も打ち切り
        if target not in self._active_shutdowns:
            return
        if await self._is_agent_alive(agent):
            log.warning("shutdown verification: %s still alive after delay+90s", target)
            try:
                await self.notify_error(
                    f"{name}のシャットダウンが効いていないようです。"
                    f"veto されたアプリが残っている可能性があります。"
                )
            except Exception:
                pass
            return

        await asyncio.sleep(300)
        if await self._is_agent_alive(agent):
            log.warning("shutdown verification: %s came back online within 5min (reboot detected)", target)
            try:
                await self.notify_error(
                    f"{name}がシャットダウン後5分以内に再起動しました。"
                    f"shutdown が reboot にすり替わった可能性があります"
                    f"（Windows Update 保留 / veto 事故）。"
                )
            except Exception:
                pass

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
