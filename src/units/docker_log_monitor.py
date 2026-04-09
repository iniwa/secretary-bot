"""Docker コンテナログ監視ユニット。

定期的に Docker コンテナのログをチェックし、エラーを検出したら Discord に通知する。
除外パターンをチャット経由で管理可能。
"""

import asyncio
import hashlib
import json
import re
import struct
import time

import httpx

from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.units.base_unit import BaseUnit

log = get_logger(__name__)

# エラー検出パターン
_ERROR_PATTERNS = [
    re.compile(r'\b(ERROR|CRITICAL|FATAL)\b'),
    re.compile(r'\bTraceback \(most recent call last\)'),
    re.compile(r'"level"\s*:\s*"(ERROR|CRITICAL|FATAL)"', re.IGNORECASE),
]

# Docker API のログで無視するノイズ（デフォルト除外）
_DEFAULT_IGNORES = [
    "Migration stmt skipped",  # DB マイグレーション既適用時のログ
]

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


class DockerLogMonitorUnit(BaseUnit):
    UNIT_NAME = "docker_log_monitor"
    UNIT_DESCRIPTION = (
        "Dockerコンテナログの監視。エラー検出時にDiscord通知。"
        "除外パターンの追加・削除・一覧表示も可能。"
    )

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("docker_monitor", {})
        self._enabled = cfg.get("enabled", True)
        self._interval = cfg.get("check_interval_seconds", 60)
        self._cooldown = cfg.get("cooldown_minutes", 30) * 60
        self._containers_filter = cfg.get("containers", [])  # 空=全コンテナ
        self._max_lines = cfg.get("max_lines_per_check", 200)

        # 最後にチェックした時刻（Unix timestamp）
        self._last_check: float = time.time()
        # 通知済みエラーのクールダウンキャッシュ {hash: timestamp}
        self._notified_cache: dict[str, float] = {}
        # 監視ループタスク
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
        exclusion_count = await self.bot.database.fetchone(
            "SELECT COUNT(*) as cnt FROM docker_log_exclusions"
        )
        cnt = exclusion_count["cnt"] if exclusion_count else 0
        return (
            f"Docker ログ監視: **稼働中**\n"
            f"チェック間隔: {self._interval}秒\n"
            f"クールダウン: {self._cooldown // 60}分\n"
            f"除外パターン数: {cnt}"
        )

    # ================================================================
    # 定期監視ループ
    # ================================================================
    async def _monitor_loop(self) -> None:
        await asyncio.sleep(30)  # 起動直後の安定待ち
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

        # 除外パターン取得
        rows = await self.bot.database.fetchall(
            "SELECT pattern FROM docker_log_exclusions"
        )
        exclusions = [r["pattern"] for r in rows] + _DEFAULT_IGNORES

        try:
            containers = await self._docker_api_get(f"/{_API_VERSION}/containers/json")
        except Exception as e:
            log.debug("Docker API unavailable: %s", e)
            return

        errors: list[dict] = []

        for c in containers:
            name = (c.get("Names") or ["/unknown"])[0].lstrip("/")

            # フィルターが設定されていて、対象外なら skip
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
                if not self._should_notify(name, line):
                    continue
                errors.append({"container": name, "message": line})

        if errors:
            await self._send_notification(errors)

    # ================================================================
    # Docker Engine API
    # ================================================================
    async def _docker_api_get(self, path: str, params: dict | None = None) -> list | dict:
        transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCKET)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://docker", timeout=10.0,
        ) as client:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _docker_api_get_raw(self, path: str, params: dict | None = None) -> bytes:
        transport = httpx.AsyncHTTPTransport(uds=_DOCKER_SOCKET)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://docker", timeout=10.0,
        ) as client:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.content

    # ================================================================
    # ログパーサー
    # ================================================================
    @staticmethod
    def _parse_docker_logs(raw: bytes) -> list[str]:
        """Docker の多重化ログストリームをパースして行リストを返す。

        TTY なしの場合: 8バイトヘッダー + ペイロードのフレーム構造
        TTY ありの場合: プレーンテキスト
        """
        lines: list[str] = []
        pos = 0
        valid_frames = 0

        while pos + 8 <= len(raw):
            stream_type = raw[pos]
            if stream_type not in (0, 1, 2):
                break  # フレームヘッダーでない → プレーンテキスト
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

        # フレーム解析が失敗した場合はプレーンテキストとしてフォールバック
        if not valid_frames and raw:
            text = raw.decode("utf-8", errors="replace")
            lines = [l.strip() for l in text.split("\n") if l.strip()]

        return lines

    # ================================================================
    # エラー判定
    # ================================================================
    @staticmethod
    def _is_error_line(line: str) -> bool:
        return any(p.search(line) for p in _ERROR_PATTERNS)

    @staticmethod
    def _is_excluded(line: str, exclusions: list[str]) -> bool:
        lower = line.lower()
        return any(pat.lower() in lower for pat in exclusions)

    def _should_notify(self, container: str, message: str) -> bool:
        """同一エラーのクールダウン判定。"""
        key = hashlib.md5(f"{container}:{message[:200]}".encode()).hexdigest()
        now = time.time()
        last = self._notified_cache.get(key, 0)
        if now - last < self._cooldown:
            return False
        self._notified_cache[key] = now
        # キャッシュ肥大化防止: 古いエントリを削除
        if len(self._notified_cache) > 1000:
            cutoff = now - self._cooldown
            self._notified_cache = {
                k: v for k, v in self._notified_cache.items() if v > cutoff
            }
        return True

    # ================================================================
    # 通知
    # ================================================================
    async def _send_notification(self, errors: list[dict]) -> None:
        # コンテナごとにグルーピング
        by_container: dict[str, list[str]] = {}
        for e in errors:
            by_container.setdefault(e["container"], []).append(e["message"])

        parts = ["**[Docker Log Alert]**"]
        for cname, msgs in by_container.items():
            parts.append(f"\n**{cname}** ({len(msgs)}件):")
            for msg in msgs[:5]:
                # メッセージを truncate して表示
                short = msg[:300] + ("…" if len(msg) > 300 else "")
                parts.append(f"```\n{short}\n```")
            if len(msgs) > 5:
                parts.append(f"  ...他 {len(msgs) - 5} 件")

        text = "\n".join(parts)
        # Discord メッセージ上限対策
        if len(text) > 1900:
            text = text[:1900] + "\n…(truncated)"
        await self.notify(text)


async def setup(bot) -> None:
    await bot.add_cog(DockerLogMonitorUnit(bot))
