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

    async def update(self) -> None:
        """背景更新フック（任意）。

        ハートビート毎に呼ばれる。重い前処理（LLM要約等）をここで済ませ、
        collect() は軽量なキャッシュ読取りだけに留める設計を推奨。
        """
        return None
