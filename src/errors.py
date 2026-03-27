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
