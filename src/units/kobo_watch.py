"""kobo_watch ユニット — 楽天 Kobo シリーズ新刊監視と通知。

Discord Skill Router からの呼び出し:
  - register    監視対象を登録
  - list        監視中の一覧
  - remove      監視対象を削除
  - check_now   即時チェック（デバッグ用）

定期実行: `on_heartbeat()` で毎時起動し、JST `check_hour_jst` ちょうどに
`_check_new_releases()` を 1 日 1 回走らせる（実行済みかは `system_state` で判定）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from src.database import JST, jst_now
from src.errors import RakutenApiError, RakutenAuthError
from src.flow_tracker import get_flow_tracker
from src.logger import get_logger
from src.rakuten import (
    BookItem,
    KoboMatch,
    RakutenApiClient,
    RakutenApiConfig,
    is_available_in_kobo,
    search_books,
)
from src.units.base_unit import BaseUnit

log = get_logger(__name__)


_EXTRACT_PROMPT = """\
あなたは新刊監視ユニットの意図抽出アシスタント。以下のユーザー入力を分析し JSON で返してください。

## アクション一覧
- register: 監視対象を登録（author 必須、title_keyword は任意）
- list:     監視中の一覧表示
- remove:   監視対象を削除（id または title_keyword で指定）
- check_now: 即時チェック（手動デバッグ用）
- update:   通知設定を変更（id 必須、enabled / notify_kobo_only のいずれか）

## 出力形式（厳守）
{{"action": "...", "author": "...", "title_keyword": "...", "id": 0, "enabled": true, "notify_kobo_only": true}}

- 不要なフィールドは省略。JSON 1 個だけ。
- author は登録時のみ必須。文中で「著者は〜」「〜先生の」等から抽出する。
- title_keyword はシリーズ名・作品名（『』『「」』内のテキストや題名らしいもの）。

## ユーザー入力
{user_input}
"""


_LAST_RUN_KEY = "kobo_watch_last_run_date"  # YYYY-MM-DD JST


class KoboWatchUnit(BaseUnit):
    UNIT_NAME = "kobo_watch"
    UNIT_DESCRIPTION = (
        "楽天 Kobo で買ってるシリーズの新刊監視。「『○○』の新刊監視して」「監視一覧」"
        "「『○○』の監視やめて」など。"
    )
    AUTONOMY_TIER = 4
    AUTONOMOUS_ACTIONS: list[str] = []

    def __init__(self, bot):
        super().__init__(bot)
        cfg = (bot.config.get("units") or {}).get(self.UNIT_NAME) or {}
        self._cfg = cfg
        self.check_hour_jst: int = int(cfg.get("check_hour_jst", 7))
        self.notify_kobo_only_default: bool = bool(cfg.get("notify_kobo_only", False))
        self.new_book_window_days: int = int(cfg.get("new_book_window_days", 60))
        api_cfg = cfg.get("api") or {}
        self.books_endpoint: str = api_cfg.get(
            "books_search_url",
            "https://openapi.rakuten.co.jp/services/api/BooksBook/Search/20170404",
        )
        self.kobo_endpoint: str = api_cfg.get(
            "kobo_search_url",
            "https://openapi.rakuten.co.jp/services/api/Kobo/EbookSearch/20170426",
        )
        match_cfg = cfg.get("kobo_match") or {}
        self.similarity_threshold: float = float(
            match_cfg.get("title_similarity_threshold", 0.80),
        )
        self.author_must_match: bool = bool(match_cfg.get("author_must_match", True))
        self.referer: str = cfg.get(
            "referer", "https://github.com/iniwa/ai-mimich-agent",
        )
        self.request_interval_ms: int = int(cfg.get("request_interval_ms", 1500))

        # 楽天 API クライアントは遅延生成（.env 未設定でもユニットロードを失敗させない）
        self._client: RakutenApiClient | None = None

    async def cog_unload(self) -> None:
        if self._client is not None:
            await self._client.close()

    # === 楽天 API クライアント遅延初期化 ===

    def _get_client(self) -> RakutenApiClient:
        if self._client is None:
            cfg = RakutenApiConfig.from_env(
                referer=self.referer, rate_limit_ms=self.request_interval_ms,
            )
            self._client = RakutenApiClient(cfg)
        return self._client

    # === Skill Router からの execute ===

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")
        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)
        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)

        message = parsed.get("message", "")
        user_id = parsed.get("user_id", "")
        try:
            extracted = await self._extract_params(message)
            action = (extracted.get("action") or "list").lower()

            if action == "register":
                result = await self._handle_register(extracted, user_id)
                self.session_done = True
            elif action == "list":
                result = await self._handle_list()
                self.session_done = False
            elif action == "remove":
                result = await self._handle_remove(extracted)
                self.session_done = True
            elif action == "update":
                result = await self._handle_update(extracted)
                self.session_done = True
            elif action == "check_now":
                result = await self._handle_check_now()
                self.session_done = True
            else:
                result = "新刊監視は登録・一覧・削除・即時チェックができるよ。何する？"
                self.session_done = False

            self.breaker.record_success()
            await ft.emit(
                "UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": action}, flow_id,
            )
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str) -> dict:
        prompt = _EXTRACT_PROMPT.format(user_input=user_input)
        return await self.llm.extract_json(prompt)

    # === intent ハンドラ ===

    async def _handle_register(self, extracted: dict, user_id: str) -> str:
        author = (extracted.get("author") or "").strip()
        title_keyword = (extracted.get("title_keyword") or "").strip() or None
        if not author:
            return "著者を教えてほしいな。「著者は○○」みたいに添えてくれる？"

        try:
            target_id = await self.bot.database.kobo_target_add(
                author=author, title_keyword=title_keyword, user_id=user_id,
                notify_kobo_only=self.notify_kobo_only_default,
            )
        except Exception as e:
            log.info("kobo_target_add duplicated or failed: %s", e)
            return "その組み合わせ、もう監視中だよ。"

        # 既刊を「既知」として backfill。楽天 API キー未設定なら 0 件で続行。
        backfilled = 0
        backfill_note = ""
        try:
            backfilled = await self._backfill_known_books(
                target_id, author, title_keyword,
            )
        except RakutenAuthError as e:
            log.warning("kobo_watch backfill skipped (no api key): %s", e)
            backfill_note = "（楽天 API キー未設定なので既刊取得はスキップ。設定後に再登録すると backfill されるよ）"
        except RakutenApiError as e:
            log.warning("kobo_watch backfill failed: %s", e)
            backfill_note = "（楽天 API でエラー: backfill はスキップしたよ）"

        kw_label = title_keyword or "(タイトル指定なし)"
        return (
            f"監視登録したよ。\n"
            f"#{target_id} 著者: {author} / タイトル: {kw_label}\n"
            f"既刊 {backfilled} 件を「既知」として保存済み。新刊が出たら通知するね。"
            f"{backfill_note}"
        )

    async def _handle_list(self) -> str:
        targets = await self.bot.database.kobo_target_list(enabled_only=False)
        if not targets:
            return "まだ監視対象が 1 件も登録されてないよ。"
        lines = ["📚 監視中の本"]
        for t in targets:
            mark = "✅" if t.get("enabled") else "⏸"
            kobo_only = " [Kobo 版のみ]" if t.get("notify_kobo_only") else ""
            kw = t.get("title_keyword") or "(タイトル指定なし)"
            lines.append(
                f"{mark} #{t['id']} {t['author']} / {kw}{kobo_only}"
            )
        return "\n".join(lines)

    async def _handle_remove(self, extracted: dict) -> str:
        target_id = extracted.get("id")
        keyword = (extracted.get("title_keyword") or extracted.get("author") or "").strip()

        target: dict | None = None
        if target_id:
            target = await self.bot.database.kobo_target_get(int(target_id))
        elif keyword:
            matches = await self.bot.database.kobo_target_find_by_keyword(keyword)
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                lines = [f"「{keyword}」に複数の候補があるよ。ID で指定してね。"]
                for m in matches:
                    kw = m.get("title_keyword") or "(指定なし)"
                    lines.append(f"  #{m['id']} {m['author']} / {kw}")
                return "\n".join(lines)

        if not target:
            return "そのタイトル、監視リストに見つからなかったよ。"

        ok = await self.bot.database.kobo_target_remove(int(target["id"]))
        if not ok:
            return f"#{target['id']} の削除に失敗したよ。"
        kw = target.get("title_keyword") or "(指定なし)"
        return f"#{target['id']} {target['author']} / {kw} の監視を解除したよ。"

    async def _handle_update(self, extracted: dict) -> str:
        target_id = extracted.get("id")
        if not target_id:
            return "更新する対象の ID を教えてね。"
        ok = await self.bot.database.kobo_target_update(
            int(target_id),
            enabled=extracted.get("enabled"),
            notify_kobo_only=extracted.get("notify_kobo_only"),
        )
        if not ok:
            return f"#{target_id} の更新に失敗したよ。"
        return f"#{target_id} の設定を更新したよ。"

    async def _handle_check_now(self) -> str:
        try:
            count = await self._check_new_releases()
        except RakutenAuthError:
            return "楽天 API キーが設定されてないみたい。.env の RAKUTEN_APPLICATION_ID と RAKUTEN_ACCESS_KEY を埋めてね。"
        except RakutenApiError as e:
            return f"楽天 API でエラー: {e}"
        return f"チェック完了。新刊 {count} 件を検出したよ。"

    # === 定期チェック ===

    async def on_heartbeat(self) -> None:
        if not self._cfg.get("enabled", True):
            return
        # 1 日 1 回起動: 当日の check_hour_jst を過ぎていて未実行ならば走らせる。
        now_jst = datetime.now(JST)
        today_str = now_jst.strftime("%Y-%m-%d")
        if now_jst.hour < self.check_hour_jst:
            return
        last_run = await self.bot.database.system_state_get(_LAST_RUN_KEY)
        if last_run == today_str:
            return
        # 楽天 API キー未設定ならスキップ（毎時 WARN を出さない）
        if not self._has_credentials():
            await self.bot.database.system_state_set(_LAST_RUN_KEY, today_str)
            log.info("kobo_watch heartbeat skipped: no rakuten api credentials")
            return
        try:
            count = await self._check_new_releases()
            log.info("kobo_watch daily check done: detected=%d", count)
        except Exception as e:
            log.error("kobo_watch daily check failed: %s", e)
        finally:
            await self.bot.database.system_state_set(_LAST_RUN_KEY, today_str)

    @staticmethod
    def _has_credentials() -> bool:
        import os
        return bool(
            os.environ.get("RAKUTEN_APPLICATION_ID")
            and os.environ.get("RAKUTEN_ACCESS_KEY"),
        )

    async def _check_new_releases(self) -> int:
        """全監視対象について新刊チェック → 通知。検出件数を返す。"""
        if self.breaker.is_open:
            log.warning("kobo_watch circuit breaker open, skip")
            return 0

        targets = await self.bot.database.kobo_target_list(enabled_only=True)
        client = self._get_client()
        detected_total = 0
        for target in targets:
            try:
                count = await self._check_one_target(target, client)
                detected_total += count
                self.breaker.record_success()
            except RakutenAuthError as e:
                # 認証エラーは即停止（個別 retry しない）
                log.error("kobo_watch auth error: %s", e)
                await self.notify_error(
                    f"楽天 API 認証エラー: {e}（.env を確認してね）",
                )
                raise
            except RakutenApiError as e:
                log.error(
                    "kobo_watch target check failed target_id=%s: %s",
                    target.get("id"), e,
                )
                self.breaker.record_failure()
                if self.breaker.is_open:
                    await self.notify_error(
                        f"楽天 API が連続失敗。kobo_watch を一時停止したよ。"
                        f"IP 変動かも？: {e}",
                    )
                    break

        # 抑制中だった通知を flush（OBS 配信終了後など）
        try:
            await self._flush_pending_notifications()
        except Exception as e:
            log.warning("flush pending notifications failed: %s", e)

        return detected_total

    async def _check_one_target(self, target: dict, client: RakutenApiClient) -> int:
        results = await search_books(
            client, title=target.get("title_keyword"),
            author=target["author"], sort="-releaseDate", hits=30,
            endpoint=self.books_endpoint,
        )

        detected = 0
        today = date.today()
        window_start = today - timedelta(days=self.new_book_window_days)
        target_id = int(target["id"])
        notify_kobo_only = bool(target.get("notify_kobo_only"))

        for book in results:
            if not book.isbn:
                continue
            if await self.bot.database.kobo_known_exists(book.isbn):
                continue

            # 発売日フィルタ: 古すぎる既刊は通知せず既知に積むだけ
            iso = book.sales_date_iso
            sales_date = _parse_iso_date(iso)
            if sales_date and sales_date < window_start:
                await self._record_known(book, target_id)
                continue

            # Kobo 版確認
            kobo_match: KoboMatch | None = None
            try:
                kobo_match = await is_available_in_kobo(
                    client,
                    paper_title=book.title, paper_author=book.author,
                    title_similarity_threshold=self.similarity_threshold,
                    author_must_match=self.author_must_match,
                    endpoint=self.kobo_endpoint,
                )
            except RakutenApiError as e:
                log.warning("kobo lookup failed for %s: %s", book.title, e)

            await self._record_known(book, target_id)
            should_notify = True
            if notify_kobo_only and kobo_match is None:
                should_notify = False

            suppressed_reason = await self._suppressed_reason()
            if not should_notify:
                suppressed_reason = "kobo_only_filter"

            detection_id = await self.bot.database.kobo_detection_record(
                isbn=book.isbn, target_id=target_id,
                kobo_available=kobo_match is not None,
                kobo_url=kobo_match.item.item_url if kobo_match else None,
                notified_at=None,
                suppressed_reason=suppressed_reason,
            )

            if should_notify and suppressed_reason is None:
                await self._notify_new_release(book, kobo_match, target.get("user_id", ""))
                await self.bot.database.kobo_detection_mark_notified(
                    detection_id, jst_now(),
                )

            detected += 1
        return detected

    async def _backfill_known_books(
        self, target_id: int, author: str, title_keyword: str | None,
    ) -> int:
        client = self._get_client()
        books = await search_books(
            client, title=title_keyword, author=author,
            sort="-releaseDate", hits=30, endpoint=self.books_endpoint,
        )
        count = 0
        for book in books:
            if book.isbn and await self._record_known(book, target_id):
                count += 1
        return count

    async def _record_known(self, book: BookItem, target_id: int) -> bool:
        return await self.bot.database.kobo_known_record(
            isbn=book.isbn, target_id=target_id,
            title=book.title, author=book.author,
            publisher=book.publisher, sales_date=book.sales_date_iso,
            item_url=book.item_url, image_url=book.image_url,
        )

    async def _suppressed_reason(self) -> str | None:
        """OBS 配信中などで通知を抑制すべきか。None なら即時通知可。"""
        detector = getattr(self.bot, "activity_detector", None)
        if detector is None:
            return None
        try:
            status = await detector.get_status()
        except Exception:
            return None
        if status.get("obs_streaming"):
            return "obs_streaming"
        return None

    async def _flush_pending_notifications(self) -> None:
        if await self._suppressed_reason() is not None:
            return
        pending = await self.bot.database.kobo_detection_list_pending()
        if not pending:
            return
        for det in pending:
            isbn = det.get("isbn")
            if not isbn:
                continue
            book_row = await self.bot.database.kobo_known_get(isbn)
            if not book_row:
                continue
            kobo_url = det.get("kobo_url")
            book = BookItem(
                isbn=book_row.get("isbn", ""),
                title=book_row.get("title", ""),
                sub_title="", series_name="",
                author=book_row.get("author", ""),
                publisher=book_row.get("publisher") or "",
                sales_date_raw="",  # raw は持たないので空。下の通知で sales_date_iso が使える
                item_url=book_row.get("item_url") or "",
                image_url=book_row.get("image_url") or "",
                item_caption="", item_price=0,
            )
            target = await self.bot.database.kobo_target_get(int(det["target_id"]))
            user_id = target.get("user_id", "") if target else ""
            await self._notify_new_release_simple(
                book, sales_date_iso=book_row.get("sales_date"),
                kobo_url=kobo_url, user_id=user_id,
            )
            await self.bot.database.kobo_detection_mark_notified(
                int(det["id"]), jst_now(),
            )

    async def _notify_new_release(
        self, book: BookItem, kobo_match: KoboMatch | None, user_id: str,
    ) -> None:
        sales_date = book.sales_date_raw or book.sales_date_iso or "未定"
        lines = [
            "📚 新刊が出てるよ！",
            "",
            f"**{book.title}**",
            f"著者: {book.author}",
            f"発売日: {sales_date}",
            f"出版社: {book.publisher or '不明'}",
            "",
        ]
        if kobo_match:
            lines.append("📱 Kobo 版配信中")
            lines.append(f"🔗 楽天 Kobo: {kobo_match.item.item_url}")
        else:
            lines.append("📕 紙のみ（Kobo 版はまだみたい）")
        if book.item_url:
            lines.append(f"🔗 楽天ブックス: {book.item_url}")
        await self.notify_user("\n".join(lines), user_id=user_id)

    async def _notify_new_release_simple(
        self, book: BookItem, *, sales_date_iso: str | None,
        kobo_url: str | None, user_id: str,
    ) -> None:
        lines = [
            "📚 新刊が出てるよ！（保留通知）",
            "",
            f"**{book.title}**",
            f"著者: {book.author}",
            f"発売日: {sales_date_iso or '未定'}",
        ]
        if kobo_url:
            lines.append(f"📱 Kobo 版: {kobo_url}")
        if book.item_url:
            lines.append(f"🔗 楽天ブックス: {book.item_url}")
        await self.notify_user("\n".join(lines), user_id=user_id)


def _parse_iso_date(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except ValueError:
        return None


async def setup(bot) -> None:
    await bot.add_cog(KoboWatchUnit(bot))
