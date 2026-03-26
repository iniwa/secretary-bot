"""ユニット単位のサーキットブレーカー。"""

import time
from dataclasses import dataclass, field

from src.errors import CircuitOpenError
from src.logger import get_logger

log = get_logger(__name__)


@dataclass
class CircuitBreaker:
    """連続失敗 → 一時停止 → 自動復帰。"""

    name: str
    failure_threshold: int = 3
    recovery_timeout: float = 60.0  # 秒

    _failure_count: int = field(default=0, init=False)
    _last_failure: float = field(default=0.0, init=False)
    _state: str = field(default="closed", init=False)  # closed / open / half_open

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            if time.monotonic() - self._last_failure >= self.recovery_timeout:
                self._state = "half_open"
                log.info("CircuitBreaker[%s] → half_open", self.name)
                return False
            return True
        return False

    def check(self) -> None:
        if self.is_open:
            raise CircuitOpenError(f"Circuit open for {self.name}")

    def record_success(self) -> None:
        if self._state == "half_open":
            log.info("CircuitBreaker[%s] → closed (recovered)", self.name)
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            log.warning("CircuitBreaker[%s] → open (%d failures)", self.name, self._failure_count)

    def reset(self) -> None:
        self._failure_count = 0
        self._state = "closed"
