"""アクティビティ統合判定。"""

import time

from src.activity.agent_monitor import AgentActivityMonitor
from src.activity.discord_monitor import DiscordVCMonitor
from src.logger import get_logger

log = get_logger(__name__)


class ActivityDetector:
    """複数ソースからアクティビティ状態を統合判定する。

    利用側:
        if await bot.activity_detector.is_blocked():
            return  # 重い処理をスキップ

        game = await bot.activity_detector.get_current_game()
    """

    def __init__(self, bot, config: dict):
        self._bot = bot
        self._config = config
        activity_cfg = config.get("activity", {})
        self._enabled = activity_cfg.get("enabled", True)
        self._block_rules = activity_cfg.get("block_rules", {})
        # active_pcs 判定のしきい値。poll 間隔の 2 倍を下限にする
        self._idle_timeout_sec = max(
            int(activity_cfg.get("poll_interval_seconds", 60)) * 2,
            int(activity_cfg.get("idle_timeout_seconds", 120)),
        )
        # Input-Relay 未起動時の WARN 抑制（1 プロセス寿命で一度だけ）
        self._ir_warn_emitted = False

        agents = config.get("windows_agents", [])
        self._agent_monitor = AgentActivityMonitor(agents)
        self._vc_monitor = DiscordVCMonitor(bot)

        # キャッシュ（ポーリング結果）
        self._cache: dict = {}

    async def close(self) -> None:
        await self._agent_monitor.close()

    async def _fetch_all(self) -> dict:
        """全ソースから最新状態を取得。"""
        agent_data = await self._agent_monitor.fetch_all()
        vc_data = self._vc_monitor.get_status()
        return {**agent_data, "vc": vc_data}

    async def get_status(self) -> dict:
        """全ソースの現在状態を返す（WebGUI表示用）。"""
        if not self._enabled:
            return {"blocked": False, "block_reason": None, "enabled": False}

        raw = await self._fetch_all()
        self._cache = raw

        main_data = raw.get("main", {})
        sub_data = raw.get("sub", {})
        vc_data = raw.get("vc", {})

        game_name = main_data.get("game")
        obs_streaming = sub_data.get("obs_streaming", False)
        obs_recording = sub_data.get("obs_recording", False)
        obs_replay_buffer = sub_data.get("obs_replay_buffer", False)
        discord_vc = vc_data.get("discord_vc", False)

        blocked, reason = self._evaluate_block(
            obs_streaming, obs_recording, obs_replay_buffer,
            game_name is not None, discord_vc,
        )

        # PC 別サマリ（Sub PC でも foreground_process / is_fullscreen を返すようになった）
        main_pc = {
            "foreground_process": main_data.get("foreground_process"),
            "is_fullscreen": bool(main_data.get("is_fullscreen", False)),
            "game": game_name,
        }
        sub_pc = {
            "foreground_process": sub_data.get("foreground_process"),
            "is_fullscreen": bool(sub_data.get("is_fullscreen", False)),
        }

        ir = main_data.get("input_relay") or None
        active_pcs = self._evaluate_active_pcs(main_data, self._idle_timeout_sec)

        return {
            "obs_connected": sub_data.get("obs_connected", False),
            "obs_streaming": obs_streaming,
            "obs_recording": obs_recording,
            "obs_replay_buffer": obs_replay_buffer,
            "gaming": {"active": game_name is not None, "game": game_name},
            # 旧互換（Main PC 基準）
            "foreground_process": main_pc["foreground_process"],
            "is_fullscreen": main_pc["is_fullscreen"],
            # PC 別サマリ（新規）
            "main": main_pc,
            "sub": sub_pc,
            "input_relay": ir,
            "active_pcs": active_pcs,
            "discord_vc": discord_vc,
            "blocked": blocked,
            "block_reason": reason,
            "enabled": True,
        }

    def _evaluate_active_pcs(self, main_data: dict, timeout_sec: int) -> list[str]:
        """Input-Relay sender の情報から現在アクティブな PC のリストを返す。

        判定ルール（`docs/design/activity_multi_pc_detection.md` 参照）:
        - gamepad イベントが idle_timeout 以内 → Main（物理的に Main 接続）
        - kbd/mouse イベントが idle_timeout 以内:
          - remote_mode=False → Main
          - remote_mode=True  → Sub
        - どれもなければ空リスト

        Input-Relay が未起動（main_data に input_relay が無い）場合は、
        従来互換として Main agent が応答していれば `["main"]` をフォールバックで返す。
        """
        ir = main_data.get("input_relay")
        if ir is None:
            # Input-Relay 未検出: Main agent から何らかの応答はあるのか？
            has_main_response = bool(main_data)
            if has_main_response and not self._ir_warn_emitted:
                log.warning("Input-Relay sender /api/status not reachable; active_pcs falls back to ['main']")
                self._ir_warn_emitted = True
            return ["main"] if has_main_response else []

        now = ir.get("server_time") or time.time()
        kbd_ts = float(ir.get("last_kbd_mouse_ts") or 0.0)
        gp_ts = float(ir.get("last_gamepad_ts") or 0.0)
        remote = bool(ir.get("remote_mode", False))

        kbd_fresh = bool(kbd_ts) and (now - kbd_ts) <= timeout_sec
        gp_fresh = bool(gp_ts) and (now - gp_ts) <= timeout_sec

        pcs: list[str] = []
        if gp_fresh:
            pcs.append("main")
        if kbd_fresh:
            pcs.append("sub" if remote else "main")
        # 重複除去（順序維持）
        seen: set[str] = set()
        return [p for p in pcs if not (p in seen or seen.add(p))]

    async def is_blocked(self) -> bool:
        """重い処理をブロックすべきかを返す。"""
        if not self._enabled:
            return False
        status = await self.get_status()
        return status["blocked"]

    async def get_current_game(self) -> str | None:
        """現在プレイ中のゲーム名を返す。"""
        raw = await self._agent_monitor.fetch_all()
        main_data = raw.get("main", {})
        return main_data.get("game")

    def _evaluate_block(
        self,
        obs_streaming: bool,
        obs_recording: bool,
        obs_replay_buffer: bool,
        gaming: bool,
        discord_vc: bool,
    ) -> tuple[bool, str | None]:
        """block_rules に照らして総合判定。"""
        rules = self._block_rules

        if obs_streaming and rules.get("obs_streaming", True):
            return True, "OBS配信中"
        if obs_recording and rules.get("obs_recording", True):
            return True, "OBS録画中"
        if obs_replay_buffer and rules.get("obs_replay_buffer", False):
            return True, "OBSリプレイバッファ有効"
        if gaming and rules.get("gaming_on_main", False):
            return True, "ゲームプレイ中"
        if discord_vc and rules.get("discord_vc", False):
            return True, "Discord VC接続中"

        return False, None
