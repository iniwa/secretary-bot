"""ContextSource 基底クラス。"""


class ContextSource:
    """InnerMind に情報を供給するソースの基底クラス。"""

    name: str = ""
    priority: int = 100
    enabled: bool = True
    # salience フィルタを回避する特権ソース（常に採用）
    always_include: bool = False

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

    async def salience(self, data: dict, shared: dict) -> float:
        """このソースが今どれだけ注目に値するか（0.0〜1.0）。

        ミミの現在の内的状態（mood, interest_topic, lens, 時間帯, energy_level）
        に応じて、このソースの情報にどれだけ注意を向けるかを返す。

        デフォルトは 0.5（中立）。各ソースは自身の性質に応じて実装を上書きする。
        """
        return 0.5

    async def update(self) -> None:
        """背景更新フック（任意）。

        ハートビート毎に呼ばれる。重い前処理（LLM要約等）をここで済ませ、
        collect() は軽量なキャッシュ読取りだけに留める設計を推奨。
        """
        return None
