"""画像生成基盤ユニット（Phase 1 Walking Skeleton）。"""

from src.units.image_gen.unit import ImageGenUnit

__all__ = ["ImageGenUnit", "setup"]


async def setup(bot) -> None:
    await bot.add_cog(ImageGenUnit(bot))
