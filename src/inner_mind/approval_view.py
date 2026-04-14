"""Discord ApprovalView — pending_actions の承認UI。"""

import re

import discord

from src.logger import get_logger

log = get_logger(__name__)

_APPROVAL_CUSTOM_ID = re.compile(r"^approval:(ok|ng):(\d+)$")


class ApprovalButton(discord.ui.Button):
    def __init__(self, label: str, style: discord.ButtonStyle, custom_id: str):
        super().__init__(label=label, style=style, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction) -> None:
        m = _APPROVAL_CUSTOM_ID.match(self.custom_id or "")
        if not m:
            await interaction.response.send_message("不正なボタンです", ephemeral=True)
            return
        decision = m.group(1)
        pending_id = int(m.group(2))
        bot = interaction.client
        actuator = getattr(bot, "actuator", None)
        if actuator is None:
            await interaction.response.send_message("Actuator 未初期化", ephemeral=True)
            return
        await actuator.handle_interaction(interaction, pending_id, decision)


class ApprovalView(discord.ui.View):
    """persistent_view。pending_id は custom_id に埋め込む。"""

    def __init__(self, pending_id: int | None = None):
        # timeout=None で persistent 化
        super().__init__(timeout=None)
        if pending_id is not None:
            self.add_item(
                ApprovalButton(
                    label="OK", style=discord.ButtonStyle.success,
                    custom_id=f"approval:ok:{pending_id}",
                ),
            )
            self.add_item(
                ApprovalButton(
                    label="NG", style=discord.ButtonStyle.danger,
                    custom_id=f"approval:ng:{pending_id}",
                ),
            )


class PersistentApprovalView(discord.ui.View):
    """Bot起動時の add_view 用。ボタンは dynamic に受ける。

    discord.py は custom_id パターンでの dynamic item をサポートしないため、
    ここではダミーの View を登録し、実際の処理は on_interaction で受ける方針を取る。
    """

    def __init__(self):
        super().__init__(timeout=None)


async def dispatch_persistent_interaction(bot, interaction: discord.Interaction) -> bool:
    """interaction.data.custom_id が approval:* なら actuator に委譲。True=処理した。"""
    data = interaction.data or {}
    custom_id = data.get("custom_id") or ""
    m = _APPROVAL_CUSTOM_ID.match(custom_id)
    if not m:
        return False
    decision = m.group(1)
    pending_id = int(m.group(2))
    actuator = getattr(bot, "actuator", None)
    if actuator is None:
        await interaction.response.send_message("Actuator 未初期化", ephemeral=True)
        return True
    await actuator.handle_interaction(interaction, pending_id, decision)
    return True
