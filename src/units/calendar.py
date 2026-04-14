"""Googleカレンダー予定登録ユニット。"""

import asyncio
import functools
import json
from datetime import datetime, timedelta

from googleapiclient.errors import HttpError

from src.database import JST, jst_now
from src.flow_tracker import get_flow_tracker
from src.gcal.service import build_calendar_service, get_service_account_email
from src.units.base_unit import BaseUnit

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

_EXTRACT_PROMPT = """\
現在日時: {now} ({weekday}曜日)

以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- create: 予定の登録（events 配列が必要）
- register_calendar: 書き込み先カレンダーIDの登録（calendar_id が必要）
- register_read_calendar: 読み取り専用カレンダーの登録（ミミに予定を読ませるため）。calendar_id、display_name、is_private を指定
- list_read_calendars: 登録済み読み取りカレンダーの一覧表示
- remove_read_calendar: 読み取りカレンダーの削除（calendar_id が必要）
- help: 使い方やカレンダーIDの確認方法などの質問

## create の events 配列の各要素
{{"summary": "予定名", "location": "場所(任意)", "description": "詳細(任意)", "start_date": "YYYY-MM-DD", "start_time": "HH:MM(終日ならnull)", "end_date": "YYYY-MM-DD(省略可)", "end_time": "HH:MM(省略可)"}}

## register_read_calendar の判定ルール
- 「Private」「非公開」「機密」「秘密」「内緒」などの語が含まれる、またはカレンダー名が "Private" の場合 → is_private: true
- それ以外は is_private: false

## 出力形式（厳守）
{{"action": "アクション名", "events": [...], "calendar_id": "xxx@group.calendar.google.com", "display_name": "表示名(任意)", "is_private": false}}

- 不要なフィールドは省略してください。
- 「今日」「明日」等の相対日付は必ずYYYY-MM-DD形式に変換してください。
- 複数の予定が含まれる場合は events 配列に複数要素を入れてください。
- end_date を省略した場合は start_date と同じ日になります。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""

_EXTRACT_WITH_PENDING_PROMPT = """\
現在日時: {now} ({weekday}曜日)

ユーザーは以前に予定登録をリクエストしましたが、一部の情報が不足していました。
以下の「保留中の予定」と「新しいユーザー入力」を統合して、完成した予定をJSON形式で返してください。

## 保留中の予定
{pending_json}

## 不足していた情報
{missing_fields}

## 出力形式（厳守）
{{"action": "create", "events": [{{"summary": "予定名", "location": "場所(任意)", "description": "詳細(任意)", "start_date": "YYYY-MM-DD", "start_time": "HH:MM(終日ならnull)", "end_date": "YYYY-MM-DD(省略可)", "end_time": "HH:MM(省略可)"}}]}}

- 保留中の予定の情報とユーザーの新しい入力を統合してください。
- 「今日」「明日」等の相対日付は必ずYYYY-MM-DD形式に変換してください。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""


class CalendarUnit(BaseUnit):
    UNIT_NAME = "calendar"
    UNIT_DESCRIPTION = (
        "Googleカレンダーへの予定登録と、ミミに予定を読ませる"
        "読み取りカレンダー（Private含む）の登録・一覧・削除。"
        "「明日14時から会議」「xxxを読み取り登録（Private）」「読み取りカレンダー一覧」など。"
    )
    AUTONOMY_TIER = 3
    AUTONOMOUS_ACTIONS = ["create_event"]
    AUTONOMY_HINT = "create_event: params={\"title\":str, \"start\":\"YYYY-MM-DD HH:MM\", \"end\":\"YYYY-MM-DD HH:MM\"}。ユーザーが予定を匂わせたが未登録の時に提案。"

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("units", {}).get("calendar", {})
        self._timezone = cfg.get("timezone", "Asia/Tokyo")
        self._service = None
        # チャネルごとの保留予定（カレンダーID未登録時やデータ不足時に一時保存）
        self._pending: dict[str, dict] = {}

    # ---- Google Calendar API ----

    def _get_service(self):
        """Google Calendar APIサービスを遅延初期化して返す。"""
        if self._service is None:
            self._service = build_calendar_service()
        return self._service

    def _get_service_account_email(self) -> str | None:
        """サービスアカウントのメールアドレスを取得する。"""
        return get_service_account_email()

    # ---- execute ----

    async def execute(self, ctx, parsed: dict) -> str | None:
        ft = get_flow_tracker()
        flow_id = parsed.get("flow_id")

        await ft.emit("CB_CHECK", "active", {"unit": self.UNIT_NAME}, flow_id)
        self.breaker.check()
        await ft.emit("CB_CHECK", "done", {"state": self.breaker.state}, flow_id)

        await ft.emit("UNIT_EXEC", "active", {"unit": self.UNIT_NAME}, flow_id)
        message = parsed.get("message", "")
        channel = parsed.get("channel", "")
        user_id = parsed.get("user_id", "")

        try:
            # 保留中の予定があり、カレンダーID登録の応答かチェック
            pending = self._pending.get(channel)

            if pending and pending.get("waiting_for") == "calendar_id":
                # カレンダーID登録を試みる
                extracted = await self._extract_params(message, channel)
                action = extracted.get("action", "")
                if action == "register_calendar" and extracted.get("calendar_id"):
                    reg_result = await self._register_calendar(extracted, user_id)
                    # 登録成功後、保留予定を実行
                    pending_extracted = pending["extracted"]
                    create_result = await self._create_events(pending_extracted, user_id)
                    self._pending.pop(channel, None)
                    result = f"{reg_result}\n\n{create_result}"
                    result = await self.personalize(result, message, flow_id)
                    self.session_done = True
                    self.breaker.record_success()
                    await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "pending_create"}, flow_id)
                    return result

            if pending and pending.get("waiting_for") == "missing_fields":
                # 不足情報の補完
                extracted = await self._extract_with_pending(
                    message, pending["extracted"], pending["missing"], channel,
                )
                extracted["action"] = "create"
                missing = self._find_missing_fields(extracted)
                if missing:
                    # まだ不足がある
                    self._pending[channel] = {
                        "extracted": extracted,
                        "missing": missing,
                        "waiting_for": "missing_fields",
                    }
                    result = self._ask_missing(missing)
                    self.session_done = False
                    self.breaker.record_success()
                    await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "ask_missing"}, flow_id)
                    return result
                # 情報が揃った
                result = await self._create_events(extracted, user_id)
                self._pending.pop(channel, None)
                result = await self.personalize(result, message, flow_id)
                self.session_done = True
                self.breaker.record_success()
                await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "create"}, flow_id)
                return result

            # 通常の新規リクエスト
            extracted = await self._extract_params(message, channel)
            action = extracted.get("action", "create")

            if action == "help":
                result = self._help_message()
                result = await self.personalize(result, message, flow_id)
                self.session_done = True
            elif action == "register_calendar":
                result = await self._register_calendar(extracted, user_id)
                result = await self.personalize(result, message, flow_id)
                self.session_done = True
            elif action == "register_read_calendar":
                result = await self._register_read_calendar(extracted)
                result = await self.personalize(result, message, flow_id)
                self.session_done = True
            elif action == "list_read_calendars":
                result = await self._list_read_calendars()
                result = await self.personalize(result, message, flow_id)
                self.session_done = True
            elif action == "remove_read_calendar":
                result = await self._remove_read_calendar(extracted)
                result = await self.personalize(result, message, flow_id)
                self.session_done = True
            else:
                # create: 不足データチェック
                missing = self._find_missing_fields(extracted)
                if missing:
                    self._pending[channel] = {
                        "extracted": extracted,
                        "missing": missing,
                        "waiting_for": "missing_fields",
                    }
                    result = self._ask_missing(missing)
                    self.session_done = False
                    self.breaker.record_success()
                    await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "ask_missing"}, flow_id)
                    return result

                # カレンダーID未登録チェック
                calendar_id = await self._get_calendar_id(user_id)
                if not calendar_id:
                    self._pending[channel] = {
                        "extracted": extracted,
                        "waiting_for": "calendar_id",
                    }
                    sa_email = self._get_service_account_email()
                    guide = "カレンダーIDがまだ登録されていません。\n"
                    guide += "カレンダーIDを教えてください。例: 「カレンダーIDは xxx@group.calendar.google.com」\n"
                    if sa_email:
                        guide += f"\nまた、Googleカレンダーの共有設定で `{sa_email}` に「予定の変更」権限を付与してください。"
                    guide += "\n\n予定の内容は覚えていますので、カレンダーIDだけ教えてもらえれば登録します。"
                    self.session_done = False
                    self.breaker.record_success()
                    await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "ask_calendar_id"}, flow_id)
                    return guide

                result = await self._create_events(extracted, user_id)
                result = await self.personalize(result, message, flow_id)
                self.session_done = True

            self.breaker.record_success()
            await ft.emit(
                "UNIT_EXEC", "done",
                {"unit": self.UNIT_NAME, "action": action}, flow_id,
            )
            return result

        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    # ---- LLM抽出 ----

    async def _extract_params(self, user_input: str, channel: str = "") -> dict:
        now = datetime.now(JST)
        weekday = _WEEKDAYS[now.weekday()]
        context = self.get_context(channel) if channel else ""
        prompt = _EXTRACT_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=weekday,
            user_input=user_input,
        )
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    async def _extract_with_pending(self, user_input: str, pending: dict, missing: list[str], channel: str = "") -> dict:
        """保留中の予定と新しい入力を統合してLLMに抽出させる。"""
        now = datetime.now(JST)
        weekday = _WEEKDAYS[now.weekday()]
        prompt = _EXTRACT_WITH_PENDING_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=weekday,
            pending_json=json.dumps(pending, ensure_ascii=False),
            missing_fields=", ".join(missing),
            user_input=user_input,
        )
        context = self.get_context(channel) if channel else ""
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    # ---- 不足フィールド検出 ----

    def _find_missing_fields(self, extracted: dict) -> list[str]:
        """events内の不足フィールドを検出する。"""
        events = extracted.get("events", [])
        if not events:
            return ["予定名", "日付"]
        missing = []
        for ev in events:
            if not ev.get("summary"):
                missing.append("予定名")
            if not ev.get("start_date"):
                missing.append("日付")
        # 重複排除
        return list(dict.fromkeys(missing))

    def _ask_missing(self, missing: list[str]) -> str:
        """不足情報をユーザーに問い合わせるメッセージを生成する。"""
        fields = "・".join(missing)
        return f"予定を登録するために、あと {fields} が必要です。教えてもらえますか？"

    # ---- ヘルプ ----

    def _help_message(self) -> str:
        sa_email = self._get_service_account_email()
        msg = (
            "【カレンダーIDの確認方法】\n"
            "1. Googleカレンダーを開く\n"
            "2. 左サイドバーで対象カレンダーの「⋮」→「設定と共有」\n"
            "3.「カレンダーの統合」セクションにある「カレンダーID」をコピー\n"
            "  （例: xxx@group.calendar.google.com）\n\n"
            "【初回セットアップ】\n"
        )
        if sa_email:
            msg += (
                f"1. 上記の「設定と共有」→「特定のユーザーとの共有」で\n"
                f"   `{sa_email}` を追加し「予定の変更」権限を付与\n"
                f"2. 「カレンダーIDは xxx@group.calendar.google.com」と伝えてください\n"
            )
        else:
            msg += "1. サービスアカウントとカレンダーを共有（「予定の変更」権限）\n"
            msg += "2. 「カレンダーIDは xxx@group.calendar.google.com」と伝えてください\n"
        msg += "\n【使い方の例】\n"
        msg += "- 「明日14時から会議を登録して」\n"
        msg += "- 「来週月曜は終日休み」\n"
        msg += "- 「明日10時に打ち合わせ、15時に歯医者」\n"
        msg += "\n【読み取りカレンダー（ミミに予定を読ませる）】\n"
        msg += "- 登録: 「abc@group.calendar.google.com を読み取り登録（Private）」\n"
        msg += "- 一覧: 「読み取りカレンダーを教えて」\n"
        msg += "- 削除: 「abc@group.calendar.google.com を読み取りから外して」\n"
        msg += "※ Private 指定した予定はタイトル非公開、時間帯のみ扱われます。"
        return msg

    # ---- カレンダーID登録 ----

    async def _register_read_calendar(self, extracted: dict) -> str:
        """読み取り専用カレンダーを calendar_read_sources に登録する。"""
        calendar_id = (extracted.get("calendar_id") or "").strip()
        if not calendar_id:
            return (
                "読み取り用カレンダーIDを教えてください。\n"
                "例: 「abc@group.calendar.google.com を読み取り登録（Private）」"
            )
        display_name = (extracted.get("display_name") or "").strip() or None
        is_private = bool(extracted.get("is_private", False))

        # サービスアカウントが本当にアクセスできるか確認
        try:
            service = self._get_service()
        except FileNotFoundError as e:
            return str(e)

        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                functools.partial(
                    service.events().list(
                        calendarId=calendar_id, maxResults=1, singleEvents=True,
                    ).execute
                ),
            )
        except HttpError as e:
            sa_email = self._get_service_account_email()
            msg = f"このカレンダーにアクセスできません（HTTP {e.resp.status}）。\n"
            if sa_email:
                msg += f"Googleカレンダーの共有設定で `{sa_email}` に「予定の表示」権限を付与してください。"
            return msg

        await self.bot.database.execute(
            "INSERT INTO calendar_read_sources "
            "(calendar_id, display_name, is_private, enabled) "
            "VALUES (?, ?, ?, 1) "
            "ON CONFLICT(calendar_id) DO UPDATE SET "
            "display_name = excluded.display_name, is_private = excluded.is_private, enabled = 1",
            (calendar_id, display_name, 1 if is_private else 0),
        )

        label = f"{display_name} " if display_name else ""
        privacy = "Private（内容非公開）" if is_private else "通常（タイトル公開）"
        return f"読み取りカレンダー {label}`{calendar_id}` を {privacy} として登録しました。"

    async def _list_read_calendars(self) -> str:
        rows = await self.bot.database.fetchall(
            "SELECT calendar_id, display_name, is_private, enabled, last_synced_at "
            "FROM calendar_read_sources ORDER BY created_at"
        )
        if not rows:
            return "読み取りカレンダーはまだ登録されていません。"
        lines = ["【読み取りカレンダー一覧】"]
        for r in rows:
            privacy = "Private" if r["is_private"] else "通常"
            status = "有効" if r["enabled"] else "無効"
            name = r["display_name"] or "(名前未設定)"
            synced = r["last_synced_at"] or "未同期"
            lines.append(f"- {name} [{privacy}/{status}] {r['calendar_id']} 最終同期: {synced}")
        return "\n".join(lines)

    async def _remove_read_calendar(self, extracted: dict) -> str:
        calendar_id = (extracted.get("calendar_id") or "").strip()
        if not calendar_id:
            return "削除するカレンダーIDを指定してください。"
        rowcount = await self.bot.database.execute_returning_rowcount(
            "DELETE FROM calendar_read_sources WHERE calendar_id = ?",
            (calendar_id,),
        )
        if rowcount == 0:
            return f"`{calendar_id}` は登録されていません。"
        await self.bot.database.execute(
            "DELETE FROM calendar_events WHERE calendar_id = ?",
            (calendar_id,),
        )
        return f"読み取りカレンダー `{calendar_id}` を削除しました。"

    async def _register_calendar(self, extracted: dict, user_id: str) -> str:
        calendar_id = extracted.get("calendar_id", "").strip()
        if not calendar_id:
            return "カレンダーIDを教えてください。\nGoogleカレンダーの設定 → カレンダーの統合 → カレンダーIDから確認できます。"

        await self.bot.database.execute(
            "INSERT INTO calendar_settings (user_id, calendar_id, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET calendar_id = excluded.calendar_id, updated_at = excluded.updated_at",
            (user_id, calendar_id, jst_now()),
        )
        return f"カレンダーID `{calendar_id}` を登録しました。"

    # ---- 予定登録 ----

    async def _get_calendar_id(self, user_id: str) -> str | None:
        row = await self.bot.database.fetchone(
            "SELECT calendar_id FROM calendar_settings WHERE user_id = ?",
            (user_id,),
        )
        return row["calendar_id"] if row else None

    async def _create_events(self, extracted: dict, user_id: str) -> str:
        events = extracted.get("events", [])
        if not events:
            return "登録する予定の内容を教えてください。例: 「明日14時から会議」"

        calendar_id = await self._get_calendar_id(user_id)
        if not calendar_id:
            sa_email = self._get_service_account_email()
            guide = "カレンダーIDが登録されていません。\n"
            guide += "まずカレンダーIDを教えてください。例: 「カレンダーIDは xxx@group.calendar.google.com」\n"
            if sa_email:
                guide += f"\nまた、Googleカレンダーの共有設定で `{sa_email}` に「予定の変更」権限を付与してください。"
            return guide

        try:
            service = self._get_service()
        except FileNotFoundError as e:
            return str(e)

        loop = asyncio.get_event_loop()
        results = []
        for ev in events:
            body = self._build_event_body(ev)
            if body is None:
                results.append(f"「{ev.get('summary', '不明')}」のフォーマットにエラーがあります。")
                continue

            try:
                created = await loop.run_in_executor(
                    None,
                    functools.partial(
                        service.events()
                        .insert(calendarId=calendar_id, body=body)
                        .execute
                    ),
                )
                summary = created.get("summary", "")
                link = created.get("htmlLink", "")
                time_str = self._format_event_time(ev)
                results.append(f"「{summary}」({time_str}) を登録しました。\n{link}")
            except HttpError as e:
                error_msg = e.content.decode("utf-8") if e.content else str(e)
                results.append(f"「{ev.get('summary', '不明')}」の登録に失敗しました: {error_msg}")

        return "\n".join(results)

    # ---- イベントボディ構築 ----

    def _build_event_body(self, ev: dict) -> dict | None:
        summary = ev.get("summary")
        if not summary:
            return None

        body: dict = {
            "summary": summary,
        }
        if ev.get("location"):
            body["location"] = ev["location"]
        if ev.get("description"):
            body["description"] = ev["description"]

        start_date = ev.get("start_date")
        start_time = ev.get("start_time")
        end_date = ev.get("end_date") or start_date
        end_time = ev.get("end_time")

        if not start_date:
            return None

        # 時間指定イベント
        if start_time:
            # end_time がない場合、start + 1時間
            if not end_time:
                try:
                    fmt = "%Y-%m-%d %H:%M"
                    dt_start = datetime.strptime(f"{start_date} {start_time}", fmt)
                except ValueError:
                    try:
                        fmt = "%Y-%m-%d %H:%M:%S"
                        dt_start = datetime.strptime(
                            f"{start_date} {start_time}", fmt
                        )
                    except ValueError:
                        return None
                dt_end = dt_start + timedelta(hours=1)
                end_date = dt_end.strftime("%Y-%m-%d")
                end_time = dt_end.strftime("%H:%M:%S")

            # HH:MM → HH:MM:SS に正規化
            if len(start_time) == 5:
                start_time = f"{start_time}:00"
            if len(end_time) == 5:
                end_time = f"{end_time}:00"

            body["start"] = {
                "dateTime": f"{start_date}T{start_time}",
                "timeZone": self._timezone,
            }
            body["end"] = {
                "dateTime": f"{end_date}T{end_time}",
                "timeZone": self._timezone,
            }
        else:
            # 終日イベント
            body["start"] = {"date": start_date}
            if not end_date or end_date == start_date:
                try:
                    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
                    dt_end = dt_start + timedelta(days=1)
                    end_date = dt_end.strftime("%Y-%m-%d")
                except ValueError:
                    return None
            body["end"] = {"date": end_date}

        return body

    # ---- 表示用 ----

    def _format_event_time(self, ev: dict) -> str:
        start_date = ev.get("start_date", "")
        start_time = ev.get("start_time")
        if start_time:
            # HH:MM:SS → HH:MM
            display_time = start_time[:5] if len(start_time) >= 5 else start_time
            return f"{start_date} {display_time}"
        return f"{start_date} 終日"


async def setup(bot) -> None:
    await bot.add_cog(CalendarUnit(bot))
