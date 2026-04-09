"""Docker コンテナログ監視ユニット。

定期的に Docker コンテナのログをチェックし、エラーを DB に保存。
WebGUI で閲覧・除外パターン管理が可能。
Discord 通知は WebGUI のトグルで有効化できる。
"""

import asyncio
import hashlib
import logging
import re
import struct
import time

import httpx

from src.database import jst_now
from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit

log = get_logger(__name__)
_httpx_logger = logging.getLogger("httpx")

# エラー検出パターン
_ERROR_PATTERNS = [
    re.compile(r'\b(ERROR|CRITICAL|FATAL)\b'),
    re.compile(r'\bTraceback \(most recent call last\)'),
    re.compile(r'"level"\s*:\s*"(ERROR|CRITICAL|FATAL)"', re.IGNORECASE),
]

# デフォルト除外パターン
_DEFAULT_IGNORES = [
    "Migration stmt skipped",
    "chromadb.telemetry",
    "posthog",
    "node_filesystem_device_error",
]

# Docker ログ行頭のタイムスタンプ除去
_TIMESTAMP_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T[\d:.]+Z?\s*')

_EXTRACT_PROMPT = """\
以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- add: 除外パターンを追加（pattern: 除外する文字列, reason: 理由）
- list: 除外パターン一覧を表示
- delete: 除外パターンを削除（pattern_id: 削除するID）
- status: 監視状態の確認

## 出力形式（厳守）
{{"action": "アクション名", "pattern": "値", "reason": "理由", "pattern_id": 0}}

- 不要なフィールドは省略してください。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""

_DOCKER_SOCKET = "/var/run/docker.sock"
_API_VERSION = "v1.43"
_SETTING_NOTIFY_DISCORD = "docker_monitor.notify_discord"


class DockerLogMonitorUnit(BaseUnit):
    UNIT_NAME = "docker_log_monitor"
    UNIT_DESCRIPTION = (
        "Dockerコンテナログの監視。エラー検出・除外パターン管理。"
        "除外パターンの追加・削除・一覧表示も可能。"
    )

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("docker_monitor", {})
        self._enabled = cfg.get("enabled", True)
        self._interval = cfg.get("check_interval_seconds", 60)
        self._cooldown = cfg.get("cooldown_minutes", 30) * 60
        self._containers_filter = cfg.get("containers", [])
        self._max_lines = cfg.get("max_lines_per_check", 200)

        self._last_check: float = time.time()
        self._seen_cache: dict[str, float] = {}  # dedup hash -> timestamp
        self._task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        if self._enabled:
            self._task = asyncio.create_task(self._monitor_loop())
            log.info("Docker log monitor started (interval=%ds)", self._interval)

    async def cog_unload(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Docker log monitor stopped")

    # ================================================================
    # execute() — チャットベースの除外パターン管理
    # ================================================================
    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        try:
            message = parsed.get("message", "")
            user_id = parsed.get("user_id", "")
            params = await self._extract_params(message)
            action = params.get("action", "status")

            if action == "add":
                result = await self._add_exclusion(
                    params.get("pattern", ""),
                    params.get("reason", ""),
                    user_id,
                )
            elif action == "delete":
                result = await self._delete_exclusion(params.get("pattern_id", 0))
            elif action == "list":
                result = await self._list_exclusions()
            else:
                result = await self._show_status()

            result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            self.session_done = True
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str) -> dict:
        context = self.get_context("discord") or ""
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        if context:
            prompt += context
        return await self.llm.extract_json(prompt)

    # ================================================================
    # 除外パターン管理
    # ================================================================
    async def _add_exclusion(self, pattern: str, reason: str, user_id: str) -> str:
        if not pattern:
            return "除外パターンを指定してください。"
        try:
            await self.bot.database.execute(
                "INSERT INTO docker_log_exclusions (pattern, reason, added_by, created_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (pattern, reason, user_id),
            )
            return f"除外パターンを追加しました: `{pattern}`"
        except Exception as e:
            if "UNIQUE" in str(e):
                return f"このパターンは既に登録されています: `{pattern}`"
            raise

    async def _delete_exclusion(self, pattern_id: int) -> str:
        if not pattern_id:
            return "削除するパターンのIDを指定してください。"
        count = await self.bot.database.execute_returning_rowcount(
            "DELETE FROM docker_log_exclusions WHERE id = ?", (pattern_id,)
        )
        if count:
            return f"除外パターン ID {pattern_id} を削除しました。"
        return f"ID {pattern_id} の除外パターンが見つかりません。"

    async def _list_exclusions(self) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT id, pattern, reason, created_at FROM docker_log_exclusions "
            "ORDER BY created_at DESC"
        )
        if not rows:
            return "除外パターンは登録されていません。"
        lines = ["**除外パターン一覧:**"]
        for r in rows:
            reason = f" ({r['reason']})" if r.get("reason") else ""
            lines.append(f"  `{r['id']}` | `{r['pattern']}`{reason}")
        return "\n".join(lines)

    async def _show_status(self) -> str:
        if not self._enabled:
            return "Docker ログ監視は無効です。"
        notify = await self.bot.database.get_setting(_SETTING_NOTIFY_DISCORD)
        exclusion_count = await self.bot.database.fetchone(
            "SELECT COUNT(*) as cnt FROM docker_log_exclusions"
        )
        error_count = await self.bot.database.fetchone(
            "SELECT COUNT(*) as cnt FROM docker_error_log WHERE dismissed = 0"
        )
        cnt_ex = exclusion_count["cnt"] if exclusion_count else 0
        cnt_err = error_count["cnt"] if error_count else 0
        return (
            f"Docker ログ監視: **稼働中**\n"
            f"チェック間隔: {self._interval}秒\n"
            f"Discord通知: {'ON' if notify == '1' else 'OFF'}\n"
            f"未対応エラー: {cnt_err}件\n"
            f"除外パターン数: {cnt_ex}"
        )

    # ================================================================
    # 定期監視ループ
    # ================================================================
    async def _monitor_loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                await self._check_logs()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("Docker log monitor error: %s", e)
            await asyncio.sleep(self._interval)

    async def _check_logs(self) -> None:
        since = self._last_check
        self._last_check = time.time()

        rows = await self.bot.database.fetchall(
            "SELECT pattern FROM docker_log_exclusions"
        )
        exclusions = [r["pattern"] for r in rows] + _DEFAULT_IGNORES

        try:
            containers = await self._docker_api_get(f"/{_API_VERSION}/containers/json")
        except Exception as e:
            log.debug("Docker API unavailable: %s", e)
            return

        new_errors: list[dict] = []

        for c in containers:
            name = (c.get("Names") or ["/unknown"])[0].lstrip("/")
            if self._containers_filter and name not in self._containers_filter:
                continue

            try:
                raw = await self._docker_api_get_raw(
                    f"/{_API_VERSION}/containers/{c['Id']}/logs",
                    params={
                        "stdout": "1", "stderr": "1",
                        "since": str(int(since)),
                        "tail": str(self._max_lines),
                        "timestamps": "1",
                    },
                )
            except Exception as e:
                log.debug("Failed to get logs for %s: %s", name, e)
                continue

            lines = self._parse_docker_logs(raw)

            for line in lines:
                if not self._is_error_line(line):
                    continue
                if self._is_excluded(line, exclusions):
                    continue
                if not self._is_new_error(name, line):
                    continue
                new_errors.append({"container": name, "message": line})

        if new_errors:
            await self._save_errors(new_errors)

            # Discord 通知トグルチェック
            notify = await self.bot.database.get_setting(_SETTING_NOTIFY_DISCORD)
            if notify == "1":
                await self._send_discord_notification(new_errors)

    # ================================================================
    # エラーの DB 保存
    # ================================================================
    async def _save_errors(self, errors: list[dict]) -> None:
        now = jst_now()
        for e in errors:
            normalized = self._normalize_message(e["message"])
            # 同一コンテナ・同一メッセージの既存レコードを更新（カウント増加）
            existing = await self.bot.database.fetchone(
                "SELECT id, count FROM docker_error_log "
                "WHERE container_name = ? AND message = ? AND dismissed = 0",
                (e["container"], normalized),
            )
            if existing:
                await self.bot.database.execute(
                    "UPDATE docker_error_log SET last_seen = ?, count = count + 1 WHERE id = ?",
                    (now, existing["id"]),
                )
            else:
                await self.bot.database.execute(
                    "INSERT INTO docker_error_log (container_name, message, first_seen, last_seen) "
                    "VALUES (?, ?, ?, ?)",
                    (e["container"], normalized, now, now),
                )

    # ================================================================
    # Docker Engine API
    # ================================================================
    async def _docker_api_get(self, path: str, params: dict | None = None) -> list | dict:
        transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCKET)
        prev = _httpx_logger.level
        _httpx_logger.setLevel(logging.WARNING)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://docker", timeout=10.0,
            ) as client:
                resp = await client.get(path, params=params)
                resp.raise_for_status()
                return resp.json()
        finally:
            _httpx_logger.setLevel(prev)

    async def _docker_api_get_raw(self, path: str, params: dict | None = None) -> bytes:
        transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCKET)
        prev = _httpx_logger.level
        _httpx_logger.setLevel(logging.WARNING)
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://docker", timeout=10.0,
            ) as client:
                resp = await client.get(path, params=params)
                resp.raise_for_status()
                return resp.content
        finally:
            _httpx_logger.setLevel(prev)

    # ================================================================
    # ログパーサー
    # ================================================================
    @staticmethod
    def _parse_docker_logs(raw: bytes) -> list[str]:
        lines: list[str] = []
        pos = 0
        valid_frames = 0

        while pos + 8 <= len(raw):
            stream_type = raw[pos]
            if stream_type not in (0, 1, 2):
                break
            size = struct.unpack(">I", raw[pos + 4 : pos + 8])[0]
            pos += 8
            if size == 0 or pos + size > len(raw):
                break
            payload = raw[pos : pos + size].decode("utf-8", errors="replace").strip()
            if payload:
                for line in payload.split("\n"):
                    line = line.strip()
                    if line:
                        lines.append(line)
            pos += size
            valid_frames += 1

        if not valid_frames and raw:
            text = raw.decode("utf-8", errors="replace")
            lines = [l.strip() for l in text.split("\n") if l.strip()]

        return lines

    # ================================================================
    # エラー判定・正規化
    # ================================================================
    @staticmethod
    def _normalize_message(message: str) -> str:
        """タイムスタンプ等を除去して正規化。DB 保存・重複判定に使用。"""
        normalized = _TIMESTAMP_RE.sub("", message)
        normalized = re.sub(r'"time"\s*:\s*"[^"]*",?\s*', "", normalized)
        return normalized.strip()

    @staticmethod
    def _is_error_line(line: str) -> bool:
        return any(p.search(line) for p in _ERROR_PATTERNS)

    @staticmethod
    def _is_excluded(line: str, exclusions: list[str]) -> bool:
        lower = line.lower()
        return any(pat.lower() in lower for pat in exclusions)

    def _is_new_error(self, container: str, message: str) -> bool:
        """クールダウン内の同一エラーを除外。"""
        normalized = self._normalize_message(message)
        key = hashlib.md5(f"{container}:{normalized[:200]}".encode()).hexdigest()
        now = time.time()
        last = self._seen_cache.get(key, 0)
        if now - last < self._cooldown:
            return False
        self._seen_cache[key] = now
        if len(self._seen_cache) > 1000:
            cutoff = now - self._cooldown
            self._seen_cache = {k: v for k, v in self._seen_cache.items() if v > cutoff}
        return True

    # ================================================================
    # Discord 通知（トグルで制御）
    # ================================================================
    async def _send_discord_notification(self, errors: list[dict]) -> None:
        by_container: dict[str, list[str]] = {}
        for e in errors:
            by_container.setdefault(e["container"], []).append(e["message"])

        parts = ["**[Docker Log Alert]**"]
        for cname, msgs in by_container.items():
            parts.append(f"\n**{cname}** ({len(msgs)}件):")
            for msg in msgs[:5]:
                short = self._normalize_message(msg)[:300]
                if len(msg) > 300:
                    short += "…"
                parts.append(f"```\n{short}\n```")
            if len(msgs) > 5:
                parts.append(f"  ...他 {len(msgs) - 5} 件")

        text = "\n".join(parts)
        if len(text) > 1900:
            text = text[:1900] + "\n…(truncated)"
        await self.notify(text)


async def setup(bot) -> None:
    await bot.add_cog(DockerLogMonitorUnit(bot))
