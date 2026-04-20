"""WebGUI のクロージャ参照値を束ねる共有コンテキスト。

`create_web_app(bot)` のクロージャで参照されていた `bot`・ロック・補助関数類を
ドメインモジュール（`src/web/routes/*.py`）から利用するための DTO。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class WebContext:
    bot: Any
    verify: Callable  # async def _verify(): pass
    webgui_lock: asyncio.Lock
    update_lock: asyncio.Lock
    agent_restart_ts: dict[str, float]
    webgui_user_id: str
    restart_window_sec: int
    mark_agent_restarting: Callable[[str], None]
    mark_agents_restarting_bulk: Callable[[list[dict]], None]
