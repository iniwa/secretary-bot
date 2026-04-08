"""リマインダー・ToDo管理ユニット。"""

from datetime import datetime, timedelta

from src.database import JST, jst_now
from src.flow_tracker import get_flow_tracker
from src.units.base_unit import BaseUnit

_WEEKDAYS = ["月", "火", "水", "木", "金", "土", "日"]

_ACTION_LABELS = {"edit": "編集", "delete": "削除", "done": "完了に"}

_CONFIRM_YES = ("はい", "うん", "yes", "ok", "おk", "おけ", "お願い", "そう", "合ってる", "合ってます", "それで", "いいよ", "いいです", "いい", "ええ", "オッケー", "頼む", "頼みます", "よろしく")
_CONFIRM_NO = ("いいえ", "いや", "no", "やめ", "キャンセル", "違う", "ちがう", "やめて", "違います", "だめ", "ダメ", "やっぱ", "やっぱり", "止め")

_EXTRACT_PROMPT = """\
現在日時: {now} ({weekday}曜日)

以下のユーザー入力を分析し、JSON形式で返してください。

## アクション一覧
- add: リマインダー登録（message と time が必要）
- list: リマインダー一覧表示
- edit: リマインダー編集（id または message_query が必要、変更する message または time を含める）
- delete: リマインダー削除（id または message_query が必要）
- done: リマインダー完了（id または message_query が必要）
- contextual_done: 会話文脈から対象リマインダーを推定して完了（「終わったよ」「できた」等）
- contextual_snooze: 会話文脈から対象リマインダーを推定して延期（「明日やる」「1週間後に言って」等、time が必要）
- ask_clarify: 対象リマインダーが特定できない場合（candidates に候補IDリストを含める）
- todo_add: ToDo追加（title が必要、due_date は任意）
- todo_list: ToDo一覧表示
- todo_done: ToDo完了（id が必要）
- todo_edit: ToDo編集（id が必要、title や due_date を変更）
- todo_delete: ToDo削除（id が必要）

## 出力形式（厳守）
{{"action": "アクション名", "message": "内容", "time": "YYYY-MM-DD HH:MM", "title": "ToDo内容", "due_date": "YYYY-MM-DD", "id": 数値, "ids": [数値], "message_query": "検索キーワード", "target_id": 数値, "candidates": [数値], "reason": "推定理由"}}

- 不要なフィールドは省略してください。
- 日時表現は必ずISO形式に変換してください。
- 複数IDの操作（例:「1と2を削除」）は ids フィールドに配列で指定してください。単一IDの場合は id を使用。
- edit/delete/done で id が分からない場合は message_query にリマインダーの内容キーワードを指定してください。
- JSON1つだけを返してください。複数のJSONを返さないでください。

## 推定ルール（contextual_done / contextual_snooze / ask_clarify）
- ユーザーの発言と会話履歴から、どのリマインダーについて話しているか推定してください
- 直近に通知されたリマインダーを優先的に候補とする
- 確信が持てる場合（1件に特定）→ target_id にそのIDを指定
- 確信が持てない場合（複数候補）→ action: "ask_clarify"、candidates に候補IDリストを含める
- 「終わったよ」「できた」等の完了表現 → action: "contextual_done"
- 「明日やる」「〇日後に言って」等の延期表現 → action: "contextual_snooze"、time に延期先の日時を指定

{reminders_context}
## ユーザー入力
{user_input}
"""


def _format_dt(remind_at: str) -> str:
    try:
        dt = datetime.fromisoformat(remind_at)
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return remind_at


class ReminderUnit(BaseUnit):
    UNIT_NAME = "reminder"
    UNIT_DESCRIPTION = "リマインダーやToDoの登録・一覧・編集・削除・完了管理。「〜時に教えて」「やることリスト」など。"

    def __init__(self, bot):
        super().__init__(bot)
        # チャネルごとの保留アクション（確認待ち）
        self._pending_actions: dict[str, dict] = {}

    # --- メイン処理 ---

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
            # 確認待ちの保留アクションがある場合
            if channel and channel in self._pending_actions:
                result = await self._handle_confirmation(channel, message, user_id)
                if result is not None:
                    result = await self.personalize(result, message, flow_id)
                    self.breaker.record_success()
                    await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": "confirm"}, flow_id)
                    return result
                # None = 確認応答と認識できなかった → 通常フローへ

            extracted = await self._extract_params(message, channel, user_id)
            action = extracted.get("action", "add")

            # list系はセッション維持（後続のID指定操作に備える）、それ以外は完了
            if action == "add":
                result = await self._add_reminder(extracted, user_id)
                self.session_done = True
            elif action == "list":
                result = await self._list_reminders(user_id)
            elif action in ("edit", "delete", "done"):
                result = await self._handle_action_with_query(action, extracted, channel, user_id)
            elif action == "contextual_done":
                result = await self._contextual_done(extracted, user_id)
                self.session_done = True
            elif action == "contextual_snooze":
                result = await self._contextual_snooze(extracted, user_id)
                self.session_done = True
            elif action == "ask_clarify":
                result = await self._ask_clarify(extracted, user_id)
                self.session_done = False
            elif action == "todo_add":
                result = await self._add_todo(extracted, user_id)
                self.session_done = True
            elif action == "todo_list":
                result = await self._list_todos(user_id)
            elif action == "todo_done":
                result = await self._done_todo(extracted, user_id)
                self.session_done = True
            elif action == "todo_edit":
                result = await self._edit_todo(extracted, user_id)
                self.session_done = True
            elif action == "todo_delete":
                result = await self._delete_todo(extracted, user_id)
                self.session_done = True
            else:
                result = await self._add_reminder(extracted, user_id)
                self.session_done = True
            if action in ("list", "todo_list"):
                result = await self.personalize_list(result, message, flow_id)
            else:
                result = await self.personalize(result, message, flow_id)
            self.breaker.record_success()
            await ft.emit("UNIT_EXEC", "done", {"unit": self.UNIT_NAME, "action": action}, flow_id)
            return result
        except Exception:
            self.breaker.record_failure()
            await ft.emit("UNIT_EXEC", "error", {"unit": self.UNIT_NAME}, flow_id)
            raise

    async def _extract_params(self, user_input: str, channel: str = "", user_id: str = "") -> dict:
        """ユーザー入力からLLMでパラメータを抽出する。"""
        now = datetime.now(JST)
        context = self.get_context(channel) if channel else ""
        reminders_context = await self._build_reminders_context(user_id)
        prompt = _EXTRACT_PROMPT.format(
            now=now.strftime("%Y-%m-%d %H:%M"),
            weekday=_WEEKDAYS[now.weekday()],
            user_input=user_input,
            reminders_context=reminders_context,
        )
        if context:
            prompt = prompt + context
        return await self.llm.extract_json(prompt)

    async def _build_reminders_context(self, user_id: str = "") -> str:
        """LLMに渡すリマインダーコンテキストを構築する。"""
        lines = []
        # 通知済み未完了リマインダー
        if user_id:
            notified = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 AND notified = 1 AND user_id = ? ORDER BY remind_at DESC",
                (user_id,),
            )
            active = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 AND user_id = ? ORDER BY remind_at",
                (user_id,),
            )
        else:
            notified = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 AND notified = 1 ORDER BY remind_at DESC"
            )
            active = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 ORDER BY remind_at"
            )
        if notified:
            lines.append("## 通知済み未完了リマインダー（直近通知順）")
            for r in notified:
                lines.append(f"- #{r['id']} {_format_dt(r['remind_at'])} 「{r['message']}」")
            lines.append("")
        if active:
            lines.append("## 全アクティブリマインダー")
            for r in active:
                status = "（通知済み）" if r.get("notified") else ""
                lines.append(f"- #{r['id']} {_format_dt(r['remind_at'])} 「{r['message']}」{status}")
            lines.append("")
        return "\n".join(lines)

    # --- 確認フロー ---

    def _check_confirmation(self, message: str) -> bool | None:
        """短文の確認応答を判定する。判定不能ならNoneを返す。"""
        msg = message.strip()
        if len(msg) > 30:
            return None
        msg_lower = msg.lower()
        if any(w in msg_lower for w in _CONFIRM_NO):
            return False
        if any(w in msg_lower for w in _CONFIRM_YES):
            return True
        return None

    async def _handle_confirmation(self, channel: str, message: str, user_id: str) -> str | None:
        """保留アクションの確認応答を処理する。判定不能ならNone（通常フローへ）。"""
        pending = self._pending_actions.pop(channel)
        confirmed = self._check_confirmation(message)

        if confirmed is None:
            return None

        if not confirmed:
            self.session_done = True
            return "キャンセルしました。"

        # 確認OK → アクション実行
        action = pending["action"]
        extracted = pending["extracted"]
        extracted["id"] = pending["reminder_id"]

        if action == "edit":
            result = await self._edit_reminder(extracted, user_id)
        elif action == "delete":
            result = await self._delete_reminder(extracted, user_id)
        else:
            result = await self._done_reminder(extracted, user_id)

        self.session_done = True
        return result

    async def _handle_action_with_query(self, action: str, extracted: dict, channel: str, user_id: str) -> str:
        """IDまたはmessage_queryでリマインダーを特定して操作する。"""
        # IDが指定されている場合は従来通り直接実行
        if extracted.get("id") or extracted.get("ids"):
            if action == "edit":
                result = await self._edit_reminder(extracted, user_id)
            elif action == "delete":
                result = await self._delete_reminder(extracted, user_id)
            else:
                result = await self._done_reminder(extracted, user_id)
            self.session_done = True
            return result

        # message_queryで検索
        query = extracted.get("message_query", "")
        if not query:
            self.session_done = True
            label = _ACTION_LABELS.get(action, action)
            return f"{label}するリマインダーのIDまたは内容を指定してください。"

        matches = await self._find_by_query(query, user_id)

        if not matches:
            self.session_done = True
            return f"「{query}」に一致するリマインダーが見つかりません。"

        if len(matches) == 1:
            # 1件マッチ → 確認待ち
            r = matches[0]
            dt_str = _format_dt(r["remind_at"])
            label = _ACTION_LABELS.get(action, action)
            self._pending_actions[channel] = {
                "action": action,
                "reminder_id": r["id"],
                "extracted": extracted,
            }
            self.session_done = False
            return f"#{r['id']} {dt_str}「{r['message']}」を{label}します。これで合っていますか？"

        # 複数マッチ → 候補表示してIDで選んでもらう
        lines = [f"「{query}」に複数のリマインダーが見つかりました。IDで指定してください。"]
        for r in matches:
            dt_str = _format_dt(r["remind_at"])
            lines.append(f"  #{r['id']}  {dt_str}  {r['message']}")
        self.session_done = False
        return "\n".join(lines)

    # --- NLP文脈操作 ---

    _SNOOZE_ESCALATION = [30, 60, 180, 360]

    async def _contextual_done(self, extracted: dict, user_id: str) -> str:
        """文脈から推定したリマインダーを完了にする。"""
        target_id = extracted.get("target_id")
        if not target_id:
            return "どのリマインダーのことか分かりませんでした。もう少し詳しく教えてください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (target_id, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1", (target_id,)
            )
        if not row:
            return f"リマインダー #{target_id} が見つかりません。"
        await self.bot.database.execute(
            "UPDATE reminders SET active = 0, done_at = ? WHERE id = ?",
            (jst_now(), target_id),
        )
        self.bot.heartbeat.cancel_reminder(target_id)
        return f"「{row['message']}」のリマインダー、完了にしました。"

    async def _contextual_snooze(self, extracted: dict, user_id: str) -> str:
        """文脈から推定したリマインダーをスヌーズする。"""
        target_id = extracted.get("target_id")
        if not target_id:
            return "どのリマインダーのことか分かりませんでした。もう少し詳しく教えてください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (target_id, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1", (target_id,)
            )
        if not row:
            return f"リマインダー #{target_id} が見つかりません。"

        time_str = extracted.get("time")
        if time_str:
            # 明示的スヌーズ: ユーザー指定の日時に再通知
            try:
                snooze_dt = datetime.fromisoformat(time_str)
            except ValueError:
                return "日時の解析ができませんでした。"
            await self.bot.database.execute(
                "UPDATE reminders SET snoozed_until = ?, snooze_count = 0 WHERE id = ?",
                (snooze_dt.isoformat(), target_id),
            )
            return f"「{row['message']}」のリマインダー、{snooze_dt.strftime('%m/%d %H:%M')} にまた通知するね。"
        else:
            # エスカレーションスヌーズ
            snooze_count = row.get("snooze_count", 0)
            idx = min(snooze_count, len(self._SNOOZE_ESCALATION) - 1)
            interval = self._SNOOZE_ESCALATION[idx]
            now = datetime.now(JST)
            await self.bot.database.execute(
                "UPDATE reminders SET snooze_count = ?, last_snoozed_at = ? WHERE id = ?",
                (snooze_count + 1, now.isoformat(), target_id),
            )
            return f"「{row['message']}」のリマインダー、{interval}分後にまた通知するね。"

    async def _ask_clarify(self, extracted: dict, user_id: str) -> str:
        """候補が複数ある場合にユーザーに確認する。"""
        candidates = extracted.get("candidates", [])
        if not candidates:
            return "どのリマインダーのことか分かりませんでした。もう少し詳しく教えてください。"
        lines = ["どれのこと？"]
        for cid in candidates:
            if user_id:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (cid, user_id)
                )
            else:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND active = 1", (cid,)
                )
            if row:
                dt_str = _format_dt(row["remind_at"])
                status = "（通知済み）" if row.get("notified") else ""
                lines.append(f"  #{row['id']}  {dt_str}  {row['message']}{status}")
        return "\n".join(lines)

    async def _find_by_query(self, query: str, user_id: str = "") -> list[dict]:
        """メッセージ内容でリマインダーをLIKE検索する。"""
        if user_id:
            return await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 AND user_id = ? AND message LIKE ? ORDER BY remind_at",
                (user_id, f"%{query}%"),
            )
        return await self.bot.database.fetchall(
            "SELECT * FROM reminders WHERE active = 1 AND message LIKE ? ORDER BY remind_at",
            (f"%{query}%",),
        )

    # --- リマインダー ---

    async def _add_reminder(self, extracted: dict, user_id: str = "") -> str:
        message = extracted.get("message", "")
        time_str = extracted.get("time", "")
        if not message:
            return "リマインドする内容を教えてください。"
        if not time_str:
            return "日時の解析ができませんでした。「明日の10時」「2025-01-01 08:00」のような形式で指定してください。"
        try:
            dt = datetime.fromisoformat(time_str)
        except ValueError:
            return "日時の解析ができませんでした。「明日の10時」「2025-01-01 08:00」のような形式で指定してください。"
        cursor = await self.bot.database.execute(
            "INSERT INTO reminders (message, remind_at, user_id) VALUES (?, ?, ?)",
            (message, dt.isoformat(), user_id),
        )
        # スケジューラにジョブ登録
        reminder_id = cursor.lastrowid if hasattr(cursor, "lastrowid") else None
        if reminder_id:
            self.bot.heartbeat.schedule_reminder(reminder_id, dt, message, user_id)
        return f"リマインダーを設定しました: {dt.strftime('%m/%d %H:%M')} に「{message}」"

    async def _list_reminders(self, user_id: str = "") -> str:
        if user_id:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 AND user_id = ? ORDER BY remind_at LIMIT 10",
                (user_id,),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM reminders WHERE active = 1 ORDER BY remind_at LIMIT 10"
            )
        if not rows:
            return "アクティブなリマインダーはありません。"
        lines = [f"\U0001f4cb リマインダー一覧（{len(rows)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            dt_str = _format_dt(r["remind_at"])
            status = " ⚠️通知済" if r.get("notified") else ""
            lines.append(f"  #{r['id']}  {dt_str}  {r['message']}{status}")
        return "\n".join(lines)

    async def _edit_reminder(self, extracted: dict, user_id: str = "") -> str:
        rid = extracted.get("id")
        if not rid:
            return "編集するリマインダーのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (rid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM reminders WHERE id = ? AND active = 1", (rid,)
            )
        if not row:
            return f"リマインダー #{rid} が見つかりません。"
        new_message = extracted.get("message") or row["message"]
        new_time_str = row["remind_at"]
        if extracted.get("time"):
            try:
                dt = datetime.fromisoformat(extracted["time"])
                new_time_str = dt.isoformat()
            except ValueError:
                return "日時の解析ができませんでした。"
        await self.bot.database.execute(
            "UPDATE reminders SET message = ?, remind_at = ?, notified = 0 WHERE id = ?",
            (new_message, new_time_str, rid),
        )
        # スケジューラのジョブを再登録
        dt_display = datetime.fromisoformat(new_time_str)
        self.bot.heartbeat.schedule_reminder(rid, dt_display, new_message, user_id)
        return f"リマインダー #{rid} を更新しました: {dt_display.strftime('%m/%d %H:%M')} に「{new_message}」"

    def _get_ids(self, extracted: dict) -> list[int]:
        """id または ids から対象IDリストを取得する。"""
        ids = extracted.get("ids", [])
        if ids:
            return [int(i) for i in ids]
        rid = extracted.get("id")
        return [int(rid)] if rid else []

    async def _delete_reminder(self, extracted: dict, user_id: str = "") -> str:
        ids = self._get_ids(extracted)
        if not ids:
            return "削除するリマインダーのIDを指定してください。"
        results = []
        for rid in ids:
            if user_id:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND user_id = ?", (rid, user_id)
                )
            else:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ?", (rid,)
                )
            if not row:
                results.append(f"#{rid} が見つかりません")
            else:
                await self.bot.database.execute("DELETE FROM reminders WHERE id = ?", (rid,))
                self.bot.heartbeat.cancel_reminder(rid)
                results.append(f"#{rid}「{row['message']}」を削除しました")
        return "\n".join(results)

    async def _done_reminder(self, extracted: dict, user_id: str = "") -> str:
        ids = self._get_ids(extracted)
        if not ids:
            return "完了にするリマインダーのIDを指定してください。"
        results = []
        for rid in ids:
            if user_id:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND active = 1 AND user_id = ?", (rid, user_id)
                )
            else:
                row = await self.bot.database.fetchone(
                    "SELECT * FROM reminders WHERE id = ? AND active = 1", (rid,)
                )
            if not row:
                results.append(f"#{rid} が見つかりません")
            else:
                await self.bot.database.execute(
                    "UPDATE reminders SET active = 0, done_at = ? WHERE id = ?",
                    (jst_now(), rid),
                )
                self.bot.heartbeat.cancel_reminder(rid)
                results.append(f"#{rid}「{row['message']}」を完了にしました")
        return "\n".join(results)

    # --- ToDo ---

    async def _add_todo(self, extracted: dict, user_id: str = "") -> str:
        title = extracted.get("title") or extracted.get("message", "")
        if not title:
            return "ToDoの内容を教えてください。"
        due_date = extracted.get("due_date") or None
        await self.bot.database.execute(
            "INSERT INTO todos (title, user_id, created_at, due_date) VALUES (?, ?, ?, ?)",
            (title, user_id, jst_now(), due_date),
        )
        due_str = f"（期限: {due_date}）" if due_date else ""
        return f"ToDoに追加しました: {title}{due_str}"

    async def _list_todos(self, user_id: str = "") -> str:
        if user_id:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM todos WHERE done = 0 AND user_id = ? ORDER BY created_at LIMIT 20",
                (user_id,),
            )
        else:
            rows = await self.bot.database.fetchall(
                "SELECT * FROM todos WHERE done = 0 ORDER BY created_at LIMIT 20"
            )
        if not rows:
            return "未完了のToDoはありません。"
        lines = [f"\U0001f4dd ToDo一覧（{len(rows)}件）", "━━━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            due = f"  📅{r['due_date'][:10]}" if r.get("due_date") else ""
            lines.append(f"  #{r['id']}  {r['title']}{due}")
        return "\n".join(lines)

    async def _done_todo(self, extracted: dict, user_id: str = "") -> str:
        todo_id = extracted.get("id")
        if not todo_id:
            return "完了するToDoのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0 AND user_id = ?", (todo_id, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0", (todo_id,)
            )
        if not row:
            return f"ToDo #{todo_id} が見つかりません。"
        await self.bot.database.execute(
            "UPDATE todos SET done = 1, done_at = ? WHERE id = ?",
            (jst_now(), todo_id),
        )
        return f"ToDo #{todo_id}「{row['title']}」を完了にしました。"

    async def _edit_todo(self, extracted: dict, user_id: str = "") -> str:
        tid = extracted.get("id")
        if not tid:
            return "編集するToDoのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0 AND user_id = ?", (tid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND done = 0", (tid,)
            )
        if not row:
            return f"ToDo #{tid} が見つかりません。"
        new_title = extracted.get("title") or row["title"]
        new_due = extracted.get("due_date") if "due_date" in extracted else row.get("due_date")
        await self.bot.database.execute(
            "UPDATE todos SET title = ?, due_date = ? WHERE id = ?", (new_title, new_due, tid)
        )
        due_str = f"（期限: {new_due}）" if new_due else ""
        return f"ToDo #{tid} を「{new_title}」{due_str}に更新しました。"

    async def _delete_todo(self, extracted: dict, user_id: str = "") -> str:
        tid = extracted.get("id")
        if not tid:
            return "削除するToDoのIDを指定してください。"
        if user_id:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ? AND user_id = ?", (tid, user_id)
            )
        else:
            row = await self.bot.database.fetchone(
                "SELECT * FROM todos WHERE id = ?", (tid,)
            )
        if not row:
            return f"ToDo #{tid} が見つかりません。"
        await self.bot.database.execute("DELETE FROM todos WHERE id = ?", (tid,))
        return f"ToDo #{tid}「{row['title']}」を削除しました。"


    # --- ハートビートでToDo通知 ---

    async def on_heartbeat(self) -> None:
        """未完了ToDoの放置通知・期限通知。"""
        now = datetime.now(JST)
        today_str = now.strftime("%Y-%m-%d")
        tomorrow = now + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")

        todos = await self.bot.database.fetchall(
            "SELECT * FROM todos WHERE done = 0 ORDER BY created_at"
        )
        if not todos:
            return

        due_today = []
        due_tomorrow = []
        stale = []  # 7日以上放置（期限なし）

        for t in todos:
            due = t.get("due_date")
            if due:
                due_day = due[:10]
                if due_day <= today_str:
                    due_today.append(t)
                elif due_day == tomorrow_str:
                    due_tomorrow.append(t)
            else:
                # 期限なし → 作成日からの経過日数チェック
                created = t.get("created_at", "")
                if created:
                    try:
                        created_dt = datetime.fromisoformat(created)
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=JST)
                        days_old = (now - created_dt).days
                        if days_old >= 7:
                            stale.append((t, days_old))
                    except Exception:
                        pass

        lines = []
        if due_today:
            lines.append("⚠️ **期限が今日のToDo:**")
            for t in due_today:
                lines.append(f"  #{t['id']} {t['title']}（期限: {t['due_date'][:10]}）")
        if due_tomorrow:
            lines.append("📅 **明日が期限のToDo:**")
            for t in due_tomorrow:
                lines.append(f"  #{t['id']} {t['title']}")
        if stale:
            lines.append(f"📋 **{len(stale)}件のToDoが7日以上未完了です:**")
            for t, days in stale[:5]:
                lines.append(f"  #{t['id']} {t['title']}（{days}日経過）")
            if len(stale) > 5:
                lines.append(f"  …他{len(stale) - 5}件")

        if lines:
            msg = "\n".join(lines)
            await self.notify(msg)


async def setup(bot) -> None:
    await bot.add_cog(ReminderUnit(bot))
