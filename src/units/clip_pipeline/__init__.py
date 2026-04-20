"""auto-kirinuki（配信切り抜き）ユニット。

旧 `streamarchive-auto-kirinuki` を secretary-bot に統合し、Pi 司令塔 +
Windows Agent 重処理の構成で動画 → Whisper 文字起こし → 音声解析 →
感情推定 → Ollama ハイライト抽出 → EDL/MP4 出力までを自動化する。

`image_gen` と同じ Pi/Agent 分離パターン:
    - Pi: ジョブ登録・状態機械（Dispatcher）・SSE pub/sub・Discord/WebGUI 連携
    - Agent: Whisper / Demucs / librosa / FFmpeg / Ollama の実行
"""

from src.units.clip_pipeline.unit import ClipPipelineUnit


async def setup(bot) -> None:
    await bot.add_cog(ClipPipelineUnit(bot))
