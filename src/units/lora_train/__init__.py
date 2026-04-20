"""LoRA 学習ユニット（Phase 4）。

WebGUI からプロジェクト管理 / データセット投入 / WD14 タグ付け / kohya 学習開始 /
進捗監視 / 学習結果の手動昇格までを統括する。
"""

from src.units.lora_train.unit import LoRATrainUnit

__all__ = ["LoRATrainUnit", "setup"]


async def setup(bot) -> None:
    await bot.add_cog(LoRATrainUnit(bot))
