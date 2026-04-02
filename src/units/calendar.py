"""Googleカレンダー予定登録ユニット。"""

import asyncio
import functools
import json
import os
from datetime import datetime, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.database import JST, jst_now
from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

_EXTRACT_PROMPT = """\
現在日時: {now} ({weekday}曜日)

以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- create: 予定の登録（events 配列が必要）
- register_calendar: カレンダーIDの登録（calendar_id が必要）
- help: 使い方やカレンダーIDの確認方法などの質問

## create の events 配列の各要素
{{"summary": "予定名", "location": "場所(任意)", "description": "詳細(任意)", "start_date": "YYYY-MM-DD", "start_time": "HH:MM(終日ならnull)", "end_date": "YYYY-MM-DD(省略可)", "end_time": "HH:MM(省略可)"}}

## 出力形式（厳守）
{{"action": "アクション名", "events": [...], "calendar_id": "xxx@group.calendar.google.com"}}

- 不要なフィールドは省略してください。
- 「今日」「明日」等の相対日付は必ずYYYY-MM-DD形式に変換してください。
- 複数の予定が含まれる場合は events 配列に複数要素を入れてください。
- end_date を省略した場合は start_date と同じ日になります。
- JSON1つだけを返してください。

## ユーザー入力
{user_input}
"""


class CalendarUnit(BaseUnit):
    UNIT_NAME = "calendar"
    UNIT_DESCRIPTION = "Googleカレンダーへの予定登録。「明日14時から会議」「来週月曜に歯医者」など。"

    def __init__(self, bot):
        super().__init__(bot)
        cfg = bot.config.get("units", {}).get("calendar", {})
        self._timezone = cfg.get("timezone", "Asia/Tokyo")
        self._sa_file = os.environ.get(
            "GOOGLE_SERVICE_ACCOUNT_FILE", "/app/data/service_account.json"
        )
        self._service = None

    # ---- Google Calendar API ----

    def _get_service(self):
        """Google Calendar APIサービスを遅延初期化して返す。"""
        if self._service is None:
            if not os.path.exists(self._sa_file):
                raise FileNotFoundError(
                    f"サービスアカウントファイルが見つかりません: {self._sa_file}"
                )
            with open(self._sa_file) as f:
                creds_data = json.load(f)
            creds = service_account.Credentials.from_service_account_info(
                creds_data, scopes=_SCOPES
            )
            self._service = build("calendar", "v3", credentials=creds)
        return self._service

    def _get_service_account_email(self) -> str | None:
        """サービスアカウントのメールアドレスを取得する。"""
        try:
            with open(self._sa_file) as f:
                return json.load(f).get("client_email")
        except (FileNotFoundError, json.JSONDecodeError):
            return None

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
            extracted = await self._extract_params(message, channel)
            action = extracted.get("action", "create")

            if action == "help":
                result = self._help_message()
            elif action == "register_calendar":
                result = await self._register_calendar(extracted, user_id)
            else:
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
        msg += "- 「明日10時に打ち合わせ、15時に歯医者」"
        return msg

    # ---- カレンダーID登録 ----

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
