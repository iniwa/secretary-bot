"""構造化ログ（JSON形式・trace_id付き）。"""

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

# マスキング対象パターン
_MASK_PATTERNS = [
    re.compile(r"(token|api_key|password|secret)[\"\']?\s*[:=]\s*[\"\']?[\w\-\.]+", re.IGNORECASE),
]


def new_trace_id() -> str:
    tid = uuid.uuid4().hex[:12]
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    return _trace_id.get()


def _mask(text: str) -> str:
    for pat in _MASK_PATTERNS:
        text = pat.sub(lambda m: m.group().split("=")[0].split(":")[0] + "=***", text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": _mask(record.getMessage()),
            "trace_id": _trace_id.get(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        return json.dumps(entry, ensure_ascii=False)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    # Windows cp932 環境で絵文字等が出力エラーになるのを防止
    import io
    stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
