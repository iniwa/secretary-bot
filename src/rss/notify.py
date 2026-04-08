"""RSS通知 — カテゴリ別ダイジェスト生成・送信。"""

from src.logger import get_logger

log = get_logger(__name__)


class RSSNotifier:
    def __init__(self, bot):
        self.bot = bot

    def format_digest(self, digest: list[dict]) -> str:
        """ダイジェストリストをDiscord向けテキストに整形。"""
        if not digest:
            return ""
        lines = ["**RSS ダイジェスト**", ""]
        for bucket in digest:
            label = bucket["label"]
            articles = bucket["articles"]
            if not articles:
                continue
            lines.append(f"__**{label}**__")
            for a in articles:
                title = a.get("title", "")[:80]
                summary = a.get("summary", "")
                url = a.get("url", "")
                lines.append(f"- **{title}**")
                if summary:
                    lines.append(f"  {summary[:120]}")
                if url:
                    lines.append(f"  <{url}>")
            lines.append("")
        return "\n".join(lines).strip()

    async def send_digest(self, digest: list[dict], user_id: str = "") -> bool:
        """ダイジェストを管理チャンネルに送信。"""
        text = self.format_digest(digest)
        if not text:
            log.info("RSS notify: no articles to send")
            return False

        import os
        channel_id = int(os.environ.get("DISCORD_ADMIN_CHANNEL_ID", "0"))
        if not channel_id:
            log.warning("RSS notify: DISCORD_ADMIN_CHANNEL_ID not set")
            return False

        channel = self.bot.get_channel(channel_id)
        if not channel:
            log.warning("RSS notify: channel %d not found", channel_id)
            return False

        # Discord 2000文字制限対応
        if len(text) > 1900:
            text = text[:1900] + "\n..."

        mention = f"<@{user_id}> " if user_id and user_id != "webgui" else ""
        await channel.send(f"{mention}{text}")
        log.info("RSS digest sent (%d chars)", len(text))
        return True
