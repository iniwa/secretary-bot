"""WebGUI単体デバッグ — Discord不要でWebGUIを起動する。

使い方:
  python debug_webgui.py

debug_runner.py で投入したテストデータを使ってWebGUIの表示を確認できる。
ブラウザで http://localhost:8100 を開く（認証なし）。
"""

import asyncio
import os
import sys
import tempfile

import uvicorn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database import Database
from src.llm.router import LLMRouter
from src.logger import setup_logging
from src.memory.chroma_client import ChromaMemory


_DEBUG_CONFIG = {
    "llm": {"ollama_model": "qwen3"},
    "gemini": {
        "conversation": False,
        "memory_extraction": False,
        "skill_routing": False,
    },
    "debug": {"dry_run": True, "verbose_logging": False, "dry_run_responses": {
        "skill_routing": '{"skill": "chat", "parsed": {"message": "test"}}',
        "conversation": "dry_run response",
        "memory_extraction": "なし",
    }},
    "units": {
        "reminder": {"enabled": True},
        "memo": {"enabled": True},
        "timer": {"enabled": True},
        "status": {"enabled": True},
        "chat": {"enabled": True},
    },
    "character": {"name": "ミミ", "persona": "テスト用"},
    "windows_agents": [],
}


class MockAgentPool:
    def __init__(self):
        self._agents = []

    async def _is_alive(self, agent):
        return False

    def get_mode(self, agent_id):
        return "auto"


class MockUnitManager:
    def __init__(self):
        self.agent_pool = MockAgentPool()
        self.units = {}

    def get(self, name):
        return self.units.get(name)


class MockBot:
    def __init__(self, config, data_dir):
        self.config = config
        self.database = Database(path=os.path.join(data_dir, "debug_bot.db"))
        self.llm_router = LLMRouter(config)
        self.chroma = ChromaMemory(path=os.path.join(data_dir, "debug_chromadb"))
        self.unit_manager = MockUnitManager()
        self.skill_router = None

    async def init(self):
        await self.database.connect()


async def main():
    setup_logging(verbose=False)
    data_dir = os.path.join(tempfile.gettempdir(), "secretary_bot_debug")
    os.makedirs(data_dir, exist_ok=True)

    bot = MockBot(_DEBUG_CONFIG, data_dir)
    await bot.init()

    from src.web.app import create_web_app
    app = create_web_app(bot)

    print(f"DB: {os.path.join(data_dir, 'debug_bot.db')}")
    print(f"WebGUI: http://localhost:8100")
    print("Ctrl+C で終了")

    config = uvicorn.Config(app, host="0.0.0.0", port=8100, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())
