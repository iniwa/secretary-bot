"""GitHubSource — ユーザーの最近のGitHub活動（commit/issue/PR）を供給。

- GITHUB_TOKEN 環境変数が必要（未設定なら enabled=False と同等に振る舞う）
- config: inner_mind.github
    username: str       監視対象のGitHubユーザー名
    lookback_hours: int 取得対象期間（既定 24）
    max_items: int      コンテキスト注入する最大件数（既定 8）
- update() で /users/{username}/events を叩き、最近の活動をキャッシュ
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx

from src.inner_mind.context_sources.base import ContextSource
from src.logger import get_logger

log = get_logger(__name__)

_API_ROOT = "https://api.github.com"


class GitHubSource(ContextSource):
    name = "GitHubの活動"
    priority = 80

    def __init__(self, bot):
        super().__init__(bot)
        self._cache: list[dict] = []
        self._cache_at: datetime | None = None
        self._lock = asyncio.Lock()

    def _cfg(self) -> dict:
        return self.bot.config.get("inner_mind", {}).get("github", {}) or {}

    def _token(self) -> str:
        return os.environ.get("GITHUB_TOKEN", "")

    def _username(self) -> str:
        return self._cfg().get("username", "") or ""

    @property
    def is_configured(self) -> bool:
        return bool(self._token()) and bool(self._username())

    async def update(self) -> None:
        if not self.is_configured:
            return
        async with self._lock:
            try:
                events = await self._fetch_events()
            except Exception as e:
                log.warning("GitHubSource fetch failed: %s", e)
                return
            self._cache = self._filter_and_format(events)
            self._cache_at = datetime.now(timezone.utc)

    async def _fetch_events(self) -> list[dict]:
        token = self._token()
        user = self._username()
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"{_API_ROOT}/users/{user}/events"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, params={"per_page": 50})
            resp.raise_for_status()
            return resp.json()

    def _filter_and_format(self, events: list[dict]) -> list[dict]:
        cfg = self._cfg()
        lookback = int(cfg.get("lookback_hours", 24))
        max_items = int(cfg.get("max_items", 8))
        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback)

        out: list[dict] = []
        for ev in events:
            try:
                ts = datetime.fromisoformat(ev["created_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if ts < cutoff:
                continue
            summary = self._summarize_event(ev)
            if summary:
                out.append({"at": ts.isoformat(), "text": summary})
            if len(out) >= max_items:
                break
        return out

    @staticmethod
    def _summarize_event(ev: dict) -> str | None:
        etype = ev.get("type", "")
        repo = ev.get("repo", {}).get("name", "")
        payload = ev.get("payload", {}) or {}
        if etype == "PushEvent":
            commits = payload.get("commits", []) or []
            n = len(commits)
            first = commits[0].get("message", "").splitlines()[0][:100] if commits else ""
            if n == 0:
                return None
            return f"[push] {repo} ({n} commits): {first}"
        if etype == "PullRequestEvent":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {}) or {}
            title = pr.get("title", "")[:120]
            return f"[PR {action}] {repo}: {title}"
        if etype == "IssuesEvent":
            action = payload.get("action", "")
            issue = payload.get("issue", {}) or {}
            title = issue.get("title", "")[:120]
            return f"[issue {action}] {repo}: {title}"
        if etype == "IssueCommentEvent":
            issue = payload.get("issue", {}) or {}
            title = issue.get("title", "")[:80]
            return f"[comment] {repo}: {title}"
        if etype == "CreateEvent":
            ref_type = payload.get("ref_type", "")
            ref = payload.get("ref", "") or ""
            return f"[create {ref_type}] {repo}{'/' + ref if ref else ''}"
        if etype == "ReleaseEvent":
            rel = payload.get("release", {}) or {}
            return f"[release] {repo}: {rel.get('tag_name', '')}"
        return None

    async def collect(self, shared: dict) -> dict | None:
        if not self.is_configured:
            return None
        if not self._cache:
            return None
        return {"events": list(self._cache)}

    def format_for_prompt(self, data: dict) -> str:
        events = data.get("events", [])
        if not events:
            return ""
        lines = []
        for e in events:
            lines.append(f"- {e['text']}")
        return "\n".join(lines)
