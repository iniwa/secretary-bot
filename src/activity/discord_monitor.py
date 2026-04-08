"""Discord VC 接続状態の検出。"""

from src.logger import get_logger

log = get_logger(__name__)


class DiscordVCMonitor:
    """Bot が参加しているギルドの VC 状態を検出する。"""

    def __init__(self, bot):
        self._bot = bot

    def is_user_in_vc(self, user_id: int | None = None) -> bool:
        """指定ユーザー（省略時は任意のメンバー）が VC に接続中か。"""
        try:
            for guild in self._bot.guilds:
                for vc in guild.voice_channels:
                    if not vc.members:
                        continue
                    if user_id is None:
                        return True
                    if any(m.id == user_id for m in vc.members):
                        return True
        except Exception as e:
            log.debug("VC check failed: %s", e)
        return False

    def get_status(self) -> dict:
        """VC の接続状態を返す。"""
        return {
            "discord_vc": self.is_user_in_vc(),
        }
