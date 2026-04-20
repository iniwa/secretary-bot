"""image_gen tool — Windows Agent 側の ComfyUI 管理 / 画像生成 / キャッシュ同期。"""
from .router import init_image_gen, router

__all__ = ["router", "init_image_gen"]
