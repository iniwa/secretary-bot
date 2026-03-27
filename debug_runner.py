"""ユニット単体デバッグランナー — Discord/LLMなしで各ユニットをテストする。

使い方:
  python debug_runner.py                    # 対話モード（ユニット選択 → parsed入力）
  python debug_runner.py memo               # memo ユニットの全シナリオを実行
  python debug_runner.py reminder add       # reminder ユニットの add アクションを実行
  python debug_runner.py --route "明日の会議をメモして"  # UnitRouter のdry_runテスト

config.yaml の debug.dry_run を true にして使用してください。
"""

import asyncio
import argparse
import json
import os
import sys
import tempfile

import yaml

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.database import Database
from src.logger import setup_logging, get_logger
from src.llm.router import LLMRouter
from src.memory.chroma_client import ChromaMemory

log = get_logger(__name__)

# --- デバッグ用config ---

_DEBUG_CONFIG = {
    "llm": {"ollama_model": "qwen3"},
    "gemini": {
        "conversation": False,
        "memory_extraction": False,
        "unit_routing": False,
    },
    "debug": {
        "dry_run": True,
        "verbose_logging": True,
        "dry_run_responses": {
            "unit_routing": '{"unit": "chat"}',
            "conversation": '{"action": "add", "message": "テスト", "time": "2026-04-01 10:00", "content": "テストメモ", "tags": "test", "keyword": "テスト", "title": "テストToDo", "id": 1, "minutes": 0.05}',
            "memory_extraction": "なし",
        },
    },
    "units": {
        "reminder": {"enabled": True},
        "memo": {"enabled": True},
        "timer": {"enabled": True},
        "status": {"enabled": True},
        "chat": {"enabled": True},
    },
    "character": {"name": "ミミ", "persona": "テスト用ペルソナ"},
    "windows_agents": [],
}


class MockChannel:
    """Discord チャンネルのモック。send()の内容を記録する。"""

    def __init__(self):
        self.id = 0
        self.messages: list[str] = []

    async def send(self, content: str) -> None:
        self.messages.append(content)
        print(f"  [Discord送信] {content}")


class MockContext:
    """Discord Context のモック。"""

    def __init__(self):
        self.channel = MockChannel()
        self.valid = False


class MockAgentPool:
    """AgentPool のモック。Windows Agent は存在しない前提。"""

    def __init__(self):
        self._agents = []

    async def _is_alive(self, agent: dict) -> bool:
        return False

    def get_mode(self, agent_id: str) -> str:
        return "auto"


class MockUnitManager:
    """UnitManager のモック。"""

    def __init__(self):
        self.agent_pool = MockAgentPool()
        self.units: dict = {}

    def get(self, name: str):
        return self.units.get(name)


class MockBot:
    """SecretaryBot のモック。DB・LLM Router・ChromaDB は実物を使う。"""

    def __init__(self, config: dict, data_dir: str):
        self.config = config
        self.database = Database(path=os.path.join(data_dir, "debug_bot.db"))
        self.llm_router = LLMRouter(config)
        self.chroma = ChromaMemory(path=os.path.join(data_dir, "debug_chromadb"))
        self.unit_manager = MockUnitManager()
        self.cogs = {}  # Cog 登録用（UnitManagerが参照する）
        self._admin_channel_id = 0
        self.user = None

    async def add_cog(self, cog) -> None:
        """discord.py の add_cog をエミュレート。"""
        self.cogs[type(cog).__name__] = cog

    def get_channel(self, channel_id: int):
        return MockChannel()


# --- ユニット直接ロード（UnitManager を経由しない） ---

def _load_unit_class(unit_name: str):
    """ユニットクラスをインポートして返す。"""
    module_map = {
        "reminder": "src.units.reminder",
        "memo": "src.units.memo",
        "timer": "src.units.timer",
        "status": "src.units.status",
        "chat": "src.units.chat",
    }
    module_path = module_map.get(unit_name)
    if not module_path:
        raise ValueError(f"Unknown unit: {unit_name}")

    import importlib
    mod = importlib.import_module(module_path)
    # モジュール内の *Unit クラスを探す
    for attr_name in dir(mod):
        cls = getattr(mod, attr_name)
        if (
            isinstance(cls, type)
            and hasattr(cls, "UNIT_NAME")
            and cls.UNIT_NAME == unit_name
        ):
            return cls
    raise ValueError(f"Unit class not found in {module_path}")


# --- テストシナリオ定義 ---

SCENARIOS: dict[str, list[dict]] = {
    "reminder": [
        {"label": "add", "parsed": {"message": "明日の10時に会議のリマインドをして"}},
        {"label": "list", "parsed": {"message": "リマインダー一覧を見せて"}},
        {"label": "edit", "parsed": {"message": "リマインダー1番を11時に変えて"}},
        {"label": "done", "parsed": {"message": "リマインダー1番を完了にして"}},
        {"label": "delete", "parsed": {"message": "リマインダー1番を削除して"}},
        {"label": "todo_add", "parsed": {"message": "買い物リスト作るをToDoに追加して"}},
        {"label": "todo_list", "parsed": {"message": "ToDo一覧を見せて"}},
        {"label": "todo_edit", "parsed": {"message": "ToDo 1番のタイトルを「洗濯物を畳む」に変えて"}},
        {"label": "todo_done", "parsed": {"message": "ToDo 1番を完了にして"}},
        {"label": "todo_delete", "parsed": {"message": "ToDo 1番を削除して"}},
    ],
    "memo": [
        {"label": "save", "parsed": {"message": "テストメモの内容をメモして"}},
        {"label": "search", "parsed": {"message": "テストのメモを検索して"}},
    ],
    "timer": [
        {"label": "start", "parsed": {"message": "3秒後にテストタイマー完了と教えて"}},
    ],
    "status": [
        {"label": "check", "parsed": {"message": "システムのステータスを確認して"}},
    ],
    "chat": [
        {"label": "chat", "parsed": {"message": "こんにちは、元気？"}},
    ],
}


async def run_unit_test(bot: MockBot, unit_name: str, parsed: dict, label: str = "") -> dict:
    """1つのユニットテストを実行し、結果を返す。"""
    ctx = MockContext()
    unit_cls = _load_unit_class(unit_name)
    unit = unit_cls(bot)

    result = {"unit": unit_name, "label": label, "parsed": parsed, "status": "OK", "response": None, "error": None}

    try:
        response = await unit.execute(ctx, parsed)
        result["response"] = response
        print(f"  [応答] {response}")
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        print(f"  [エラー] {e}")

    return result


async def run_route_test(bot: MockBot, user_input: str) -> dict:
    """UnitRouter の dry_run テスト。"""
    from src.unit_router import UnitRouter
    router = UnitRouter(bot)
    result = await router.route(user_input)
    return result


async def interactive_mode(bot: MockBot) -> list[dict]:
    """対話モードで実行。"""
    results = []
    units = list(SCENARIOS.keys())

    while True:
        print("\n--- ユニット選択 ---")
        for i, name in enumerate(units, 1):
            print(f"  {i}. {name}")
        print(f"  {len(units) + 1}. UnitRouter テスト")
        print(f"  {len(units) + 2}. 全ユニット一括テスト")
        print("  q. 終了")

        choice = input("\n選択> ").strip()
        if choice.lower() == "q":
            break

        if choice == str(len(units) + 2):
            # 全ユニット一括
            results.extend(await run_all_scenarios(bot))
            continue

        if choice == str(len(units) + 1):
            user_input = input("入力テキスト> ").strip()
            if user_input:
                route_result = await run_route_test(bot, user_input)
                print(f"  [ルーティング結果] {json.dumps(route_result, ensure_ascii=False)}")
            continue

        try:
            idx = int(choice) - 1
            unit_name = units[idx]
        except (ValueError, IndexError):
            print("無効な選択です。")
            continue

        scenarios = SCENARIOS[unit_name]
        print(f"\n--- {unit_name} シナリオ ---")
        for i, s in enumerate(scenarios, 1):
            print(f"  {i}. {s['label']}")
        print(f"  a. 全シナリオ実行")
        print(f"  c. カスタム parsed を入力")

        sc = input("選択> ").strip()
        if sc.lower() == "a":
            for s in scenarios:
                print(f"\n>> {unit_name} / {s['label']}")
                r = await run_unit_test(bot, unit_name, s["parsed"], s["label"])
                results.append(r)
        elif sc.lower() == "c":
            raw = input("parsed (JSON)> ").strip()
            try:
                parsed = json.loads(raw)
                print(f"\n>> {unit_name} / custom")
                r = await run_unit_test(bot, unit_name, parsed, "custom")
                results.append(r)
            except json.JSONDecodeError:
                print("無効なJSONです。")
        else:
            try:
                si = int(sc) - 1
                s = scenarios[si]
                print(f"\n>> {unit_name} / {s['label']}")
                r = await run_unit_test(bot, unit_name, s["parsed"], s["label"])
                results.append(r)
            except (ValueError, IndexError):
                print("無効な選択です。")

    return results


async def run_all_scenarios(bot: MockBot) -> list[dict]:
    """全ユニットの全シナリオを一括実行。"""
    results = []
    for unit_name, scenarios in SCENARIOS.items():
        print(f"\n{'='*40}")
        print(f" {unit_name}")
        print(f"{'='*40}")
        for s in scenarios:
            print(f"\n>> {s['label']}")
            r = await run_unit_test(bot, unit_name, s["parsed"], s["label"])
            results.append(r)
    return results


def print_summary(results: list[dict]) -> None:
    """テスト結果サマリーを表示。"""
    if not results:
        return
    print(f"\n{'='*50}")
    print(" テスト結果サマリー")
    print(f"{'='*50}")
    ok = sum(1 for r in results if r["status"] == "OK")
    ng = sum(1 for r in results if r["status"] == "ERROR")
    print(f"  OK: {ok}  ERROR: {ng}  TOTAL: {len(results)}")
    print()
    for r in results:
        mark = "OK" if r["status"] == "OK" else "NG"
        print(f"  [{mark}] {r['unit']}/{r['label']}")
        if r["response"]:
            # 1行目だけ表示
            first_line = r["response"].split("\n")[0]
            print(f"       -> {first_line}")
        if r["error"]:
            print(f"       !! {r['error']}")


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="ユニット単体デバッグランナー")
    parser.add_argument("unit", nargs="?", help="テストするユニット名 (reminder/memo/timer/status/chat)")
    parser.add_argument("action", nargs="?", help="アクション名 (add/list/save/search等)")
    parser.add_argument("--route", type=str, help="UnitRouterのテスト入力テキスト")
    parser.add_argument("--all", action="store_true", help="全ユニット一括テスト")
    parser.add_argument("--config", type=str, help="config.yaml のパス（省略時はデバッグ用デフォルト設定）")
    args = parser.parse_args()

    # config 読み込み
    if args.config and os.path.exists(args.config):
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        # dry_run を強制有効化
        config.setdefault("debug", {})["dry_run"] = True
    else:
        config = _DEBUG_CONFIG

    setup_logging(verbose=True)

    # 一時データディレクトリ
    data_dir = os.path.join(tempfile.gettempdir(), "secretary_bot_debug")
    os.makedirs(data_dir, exist_ok=True)

    bot = MockBot(config, data_dir)
    await bot.database.connect()
    print(f"DB: {os.path.join(data_dir, 'debug_bot.db')}")
    print(f"ChromaDB: {os.path.join(data_dir, 'debug_chromadb')}")
    print(f"dry_run: {config.get('debug', {}).get('dry_run', False)}")
    print()

    results = []

    try:
        if args.route:
            # UnitRouter テスト
            route_result = await run_route_test(bot, args.route)
            print(f"ルーティング結果: {json.dumps(route_result, ensure_ascii=False, indent=2)}")

        elif args.all:
            # 全ユニット一括
            results = await run_all_scenarios(bot)

        elif args.unit:
            # 特定ユニットのテスト
            unit_name = args.unit
            if unit_name not in SCENARIOS:
                print(f"エラー: 不明なユニット '{unit_name}'")
                print(f"利用可能: {', '.join(SCENARIOS.keys())}")
                return

            if args.action:
                # アクション指定 → 該当シナリオを探す
                scenarios = SCENARIOS[unit_name]
                matched = [s for s in scenarios if s["label"].startswith(args.action)]
                if matched:
                    for s in matched:
                        print(f">> {unit_name} / {s['label']}")
                        r = await run_unit_test(bot, unit_name, s["parsed"], s["label"])
                        results.append(r)
                else:
                    # カスタム parsed として実行
                    parsed = {"message": args.action}
                    print(f">> {unit_name} / {args.action} (カスタム)")
                    r = await run_unit_test(bot, unit_name, parsed, args.action)
                    results.append(r)
            else:
                # ユニット名のみ → 全シナリオ実行
                for s in SCENARIOS[unit_name]:
                    print(f">> {unit_name} / {s['label']}")
                    r = await run_unit_test(bot, unit_name, s["parsed"], s["label"])
                    results.append(r)

        else:
            # 対話モード
            results = await interactive_mode(bot)

    finally:
        await bot.database.close()
        print_summary(results)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
