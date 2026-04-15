"""image_gen tool — Windows Agent 側の ComfyUI 管理 / 画像生成 / キャッシュ同期。"""
from .router import router, init_image_gen

__all__ = ["router", "init_image_gen"]
