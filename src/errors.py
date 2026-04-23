"""エラークラス定義 — 全エラーは BotError を継承する。"""

from enum import Enum


class Severity(Enum):
    LOW = "low"          # ログのみ
    MEDIUM = "medium"    # Discord管理チャンネルに通知
    HIGH = "high"        # Discord通知 + 詳細ログ


class BotError(Exception):
    """Bot全体の基底エラー。"""

    severity: Severity = Severity.MEDIUM

    def __init__(self, message: str, *, severity: Severity | None = None):
        super().__init__(message)
        if severity is not None:
            self.severity = severity


class LLMError(BotError):
    """LLM呼び出しに関するエラー。"""
    severity = Severity.MEDIUM


class OllamaUnavailableError(LLMError):
    """Ollamaに接続できない。"""
    severity = Severity.LOW


class GeminiError(LLMError):
    """Gemini API呼び出しエラー。"""
    severity = Severity.MEDIUM


class AllLLMsUnavailableError(LLMError):
    """全LLMが利用不可。"""
    severity = Severity.HIGH


class LLMJsonParseError(LLMError):
    """LLM出力のJSON解析に失敗。"""
    severity = Severity.MEDIUM


class DatabaseError(BotError):
    """DB操作エラー。"""
    severity = Severity.HIGH


class UnitError(BotError):
    """ユニット実行エラー。"""
    severity = Severity.MEDIUM


class DelegationError(BotError):
    """Windows委託エラー。"""
    severity = Severity.MEDIUM


class AgentUnavailableError(DelegationError):
    """Windows Agentに接続できない。"""
    severity = Severity.LOW


class CircuitOpenError(UnitError):
    """サーキットブレーカーが開いている。"""
    severity = Severity.LOW


class ConfigError(BotError):
    """設定ファイルエラー。"""
    severity = Severity.HIGH


# === 画像生成基盤エラー階層 ===


class ImageGenError(BotError):
    """画像生成基盤の基底エラー。"""
    severity = Severity.MEDIUM


class ValidationError(ImageGenError):
    """入力不正・必須モデル欠損など。retry 不可。"""
    severity = Severity.LOW


class TransientError(ImageGenError):
    """通信一時エラーなど。retry 可能。"""
    severity = Severity.LOW


class ResourceUnavailableError(TransientError):
    """Agent 全滅・NAS 切断など。retry 可能（長め backoff）。"""
    severity = Severity.MEDIUM


class CacheSyncError(TransientError):
    """キャッシュ同期失敗。retry 可能（sha256 不一致で自動再 sync）。"""
    severity = Severity.MEDIUM


class AgentCommunicationError(TransientError):
    """Windows Agent との通信失敗。retry 可能。"""
    severity = Severity.LOW


class ComfyUIError(ImageGenError):
    """ComfyUI 実行時エラー（既定は retry 不可）。"""
    severity = Severity.MEDIUM


class OOMError(ComfyUIError):
    """VRAM 不足。別 Agent への retry を推奨。"""
    severity = Severity.LOW
    # OOM は同一 Agent では再試行しないが、別 Agent で retry 可能扱い


class WorkflowValidationError(ComfyUIError):
    """ComfyUI がワークフローを拒否。retry 不可。"""
    severity = Severity.LOW


# === auto-kirinuki（配信アーカイブ切り抜き）エラー階層 ===
# TransientError / CacheSyncError / AgentCommunicationError は image_gen と共用する。
# ここでは clip_pipeline 固有のドメインエラーのみ定義する。


class ClipPipelineError(BotError):
    """配信アーカイブ切り抜きパイプラインの基底エラー。"""
    severity = Severity.MEDIUM


class WhisperError(ClipPipelineError):
    """Whisper 推論エラー（モデルロード失敗・GPU OOM など）。"""
    severity = Severity.MEDIUM


class TranscribeError(ClipPipelineError):
    """文字起こし処理の失敗（segments 取得失敗・空結果など）。"""
    severity = Severity.MEDIUM


class HighlightError(ClipPipelineError):
    """ハイライト抽出の失敗（LLM 応答不正・EDL 生成失敗など）。"""
    severity = Severity.MEDIUM


# === 楽天 Web Service / kobo_watch エラー階層 ===


class RakutenApiError(BotError):
    """楽天 API 呼び出しの基底エラー。"""
    severity = Severity.MEDIUM

    def __init__(
        self, message: str, *, status: int | None = None,
        body: str | None = None, severity: Severity | None = None,
    ):
        super().__init__(message, severity=severity)
        self.status = status
        self.body = body


class RakutenAuthError(RakutenApiError):
    """applicationId / accessKey 不正・必須欠落（HTTP 400/401）。"""
    severity = Severity.HIGH


class RakutenRefererError(RakutenApiError):
    """Referer ヘッダー不足 or Allow IP 不一致（HTTP 403）。"""
    severity = Severity.HIGH


class RakutenRateLimitError(RakutenApiError):
    """レート制限超過（HTTP 429）。retry 可能。"""
    severity = Severity.LOW


class KoboWatchError(BotError):
    """kobo_watch ユニット固有のエラー。"""
    severity = Severity.MEDIUM


# retry 判定の集約ヘルパー
_RETRYABLE_CLASSES = (
    TransientError, ResourceUnavailableError, CacheSyncError,
    AgentCommunicationError, OOMError,
    RakutenRateLimitError,
)


def is_retryable(exc: BaseException) -> bool:
    """例外が retry 可能かを判定する。OOM は別 Agent 前提。"""
    return isinstance(exc, _RETRYABLE_CLASSES)
