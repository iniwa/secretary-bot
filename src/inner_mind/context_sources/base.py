"""ContextSource 基底クラス。"""


class ContextSource:
    """InnerMind に情報を供給するソースの基底クラス。"""

    name: str = ""
    priority: int = 100
    enabled: bool = True

    def __init__(self, bot):
        self.bot = bot

    async def collect(self, shared: dict) -> dict | None:
        """コンテキストを収集して返す。データなしなら None。

        shared: 他のソースと共有するコンテキスト（前回モノローグ・直近会話要約等）。
        """
        raise NotImplementedError

    def format_for_prompt(self, data: dict) -> str:
        """収集データをLLMプロンプト用テキストに変換。"""
        raise NotImplementedError
