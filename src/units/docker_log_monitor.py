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

# 警告検出パターン（エラーより緩め、通知は出さない別セクション扱い）
_WARN_PATTERNS = [
    re.compile(r'\bWARN(ING)?\b'),
    re.compile(r'"level"\s*:\s*"WARN(ING)?"', re.IGNORECASE),
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
                "INSERT INTO docker_log_exclusions "
                "(container_name, pattern, reason, added_by, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("", pattern, reason, user_id, jst_now()),
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
        error_count = await self.bot.database.fetchone(
            "SELECT COUNT(*) as cnt FROM docker_error_log "
            "WHERE dismissed = 0 AND level = 'error'"
        )
        warn_count = await self.bot.database.fetchone(
            "SELECT COUNT(*) as cnt FROM docker_error_log "
            "WHERE dismissed = 0 AND level = 'warning'"
        )
        cnt_ex = exclusion_count["cnt"] if exclusion_count else 0
        cnt_err = error_count["cnt"] if error_count else 0
        cnt_warn = warn_count["cnt"] if warn_count else 0
        return (
            f"Docker ログ監視: **稼働中**\n"
            f"チェック間隔: {self._interval}秒\n"
            f"未対応エラー: {cnt_err}件\n"
            f"未対応警告: {cnt_warn}件\n"
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
            "SELECT container_name, pattern FROM docker_log_exclusions"
        )
        # DB除外: (container_name, pattern) のタプルリスト
        db_exclusions = [
            (r.get("container_name", ""), r["pattern"]) for r in rows
        ]
        # デフォルト除外: コンテナ名なし（全コンテナに適用）
        default_exclusions = [("", pat) for pat in _DEFAULT_IGNORES]
        exclusions = db_exclusions + default_exclusions

        try:
            containers = await self._docker_api_get(f"/{_API_VERSION}/containers/json")
        except Exception as e:
            log.debug("Docker API unavailable: %s", e)
            return

        new_errors: list[dict] = []
        new_warnings: list[dict] = []

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
                level = self._classify_line(line)
                if level is None:
                    continue
                if self._is_excluded(name, line, exclusions):
                    continue
                if not self._is_new_error(name, line):
                    continue
                entry = {"container": name, "message": line, "level": level}
                if level == "error":
                    new_errors.append(entry)
                else:
                    new_warnings.append(entry)

        if new_errors:
            await self._save_entries(new_errors)
            # エラーは常にミミちゃん口調で通知
            await self._send_mimi_alert(new_errors)

        if new_warnings:
            await self._save_entries(new_warnings)
            # 警告は保存のみ、通知は出さない

    # ================================================================
    # エラー/警告 の DB 保存
    # ================================================================
    async def _save_entries(self, entries: list[dict]) -> None:
        """error / warning を同一テーブルに level 付きで保存する。"""
        now = jst_now()
        for e in entries:
            normalized = self._normalize_message(e["message"])
            level = e.get("level", "error")
            # 同一コンテナ・同一メッセージ・同一レベルの既存レコードを更新
            existing = await self.bot.database.fetchone(
                "SELECT id, count FROM docker_error_log "
                "WHERE container_name = ? AND message = ? AND level = ? AND dismissed = 0",
                (e["container"], normalized, level),
            )
            if existing:
                await self.bot.database.execute(
                    "UPDATE docker_error_log SET last_seen = ?, count = count + 1 WHERE id = ?",
                    (now, existing["id"]),
                )
            else:
                await self.bot.database.execute(
                    "INSERT INTO docker_error_log "
                    "(container_name, message, first_seen, last_seen, level) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (e["container"], normalized, now, now, level),
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
            lines = [line.strip() for line in text.split("\n") if line.strip()]

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
    def _classify_line(line: str) -> str | None:
        """行を error / warning に分類。該当しなければ None。
        error パターンを優先（WARNING を含んでも ERROR があれば error 扱い）。"""
        if any(p.search(line) for p in _ERROR_PATTERNS):
            return "error"
        if any(p.search(line) for p in _WARN_PATTERNS):
            return "warning"
        return None

    @staticmethod
    def _is_excluded(
        container: str, line: str, exclusions: list[tuple[str, str]]
    ) -> bool:
        lower_line = line.lower()
        lower_container = container.lower()
        for exc_container, exc_pattern in exclusions:
            if exc_container and exc_container.lower() != lower_container:
                continue  # コンテナ名指定あり & 不一致 → スキップ
            if exc_pattern.lower() in lower_line:
                return True
        return False

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
    # Discord 通知（エラーのみ、ミミちゃん口調で常時通知）
    # ================================================================
    async def _send_mimi_alert(self, errors: list[dict]) -> None:
        """新規エラーを Mimi ペルソナで Discord に通知する。警告は含めない。"""
        # 生のサマリを作る（コンテナ別にグルーピング）
        by_container: dict[str, list[str]] = {}
        for e in errors:
            by_container.setdefault(e["container"], []).append(e["message"])

        summary_parts: list[str] = []
        for cname, msgs in by_container.items():
            summary_parts.append(f"【{cname}】 {len(msgs)} 件")
            for msg in msgs[:3]:
                short = self._normalize_message(msg)[:200]
                summary_parts.append(f"  - {short}")
            if len(msgs) > 3:
                summary_parts.append(f"  …他 {len(msgs) - 3} 件")
        raw_summary = "\n".join(summary_parts)

        # Ollama でペルソナ変換を試み、失敗時は生サマリを送る
        text = await self._mimi_voice(raw_summary)
        if len(text) > 1900:
            text = text[:1900] + "\n…(truncated)"
        await self.notify(text)

    async def _mimi_voice(self, raw_summary: str) -> str:
        """Ollama 稼働時はミミちゃん口調に変換、それ以外は素のテキストを返す。
        BaseUnit.personalize はユーザー発言が前提なので、自律通知用に直接 llm を叩く。"""
        header = "**[Docker Error Alert]**\n"
        fallback = header + raw_summary
        if not self.bot.llm_router.ollama_available:
            return fallback
        persona = self.bot.config.get("character", {}).get("persona", "")
        if not persona:
            return fallback
        try:
            system = (
                f"{persona}\n\n"
                "あなたが監視している Docker コンテナで新しいエラーを検出しました。"
                "マスターにキャラクターらしい口調で短く自然に伝えてください。"
                "コンテナ名と件数は正確に残し、冗長にならないよう 3〜5 文以内で。"
                "技術情報（エラーメッセージの内容）はそのまま引用して構いません。"
            )
            prompt = (
                "検出された Docker エラー:\n"
                f"{raw_summary}\n\n"
                "このエラー発生をマスターに知らせる一言を生成してください。"
            )
            generated = await self.llm.generate(prompt, system=system)
            if not generated:
                return fallback
            return header + generated.strip()
        except Exception as e:
            log.warning("Mimi voice generation failed: %s", e)
            return fallback


async def setup(bot) -> None:
    await bot.add_cog(DockerLogMonitorUnit(bot))
