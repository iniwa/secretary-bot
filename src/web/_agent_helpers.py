"""Windows Agent / Portainer 連携の共通ヘルパー。

元 `src/web/app.py` のクロージャ内にあった関数群をモジュールへ移動。
挙動・ログフォーマットは完全に保持している。
"""

from __future__ import annotations

import asyncio
import os

import httpx

from src.logger import get_logger

log = get_logger(__name__)


async def restart_container() -> dict:
    """Portainer API 経由でコンテナを再起動する。{"restarted": bool, "detail": str} を返す。"""
    portainer_url = os.environ.get("PORTAINER_URL", "")
    portainer_token = os.environ.get("PORTAINER_API_TOKEN", "")
    if not (portainer_url and portainer_token):
        msg = "Portainer設定なし（PORTAINER_URL / PORTAINER_API_TOKEN）"
        log.warning("Portainer env vars not set — skipping restart")
        return {"restarted": False, "detail": msg}
    try:
        env_id = os.environ.get("PORTAINER_ENV_ID", "1")
        container_name = os.environ.get("CONTAINER_NAME", "secretary-bot")
        headers = {"X-API-Key": portainer_token}
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            filters = f'{{"name":["{container_name}"]}}'
            list_resp = await client.get(
                f"{portainer_url}/api/endpoints/{env_id}/docker/containers/json",
                headers=headers,
                params={"filters": filters},
            )
            list_resp.raise_for_status()
            containers = list_resp.json()
            if not containers:
                msg = f"コンテナ '{container_name}' が見つかりません"
                log.error("Container not found: %s", container_name)
                return {"restarted": False, "detail": msg}
            container_id = containers[0]["Id"]
            restart_resp = await client.post(
                f"{portainer_url}/api/endpoints/{env_id}/docker/containers/{container_id}/restart",
                headers=headers,
            )
            if restart_resp.status_code < 300:
                log.info("Container restarted: %s", container_name)
                return {"restarted": True, "detail": f"コンテナ '{container_name}' を再起動しました"}
            else:
                msg = f"再起動API エラー (HTTP {restart_resp.status_code}): {restart_resp.text[:200]}"
                log.error("Container restart failed: %s %s", restart_resp.status_code, restart_resp.text)
                return {"restarted": False, "detail": msg}
    except Exception as e:
        log.error("Portainer API error: %s", e)
        return {"restarted": False, "detail": f"Portainer API 接続失敗: {e}"}


async def delayed_restart(delay_seconds: float = 2):
    """レスポンス送信後に遅延してコンテナを再起動する。"""
    await asyncio.sleep(delay_seconds)
    await restart_container()


async def post_all_agents(bot, path: str, timeout: float = 8) -> list[dict]:
    """全Windows Agent の指定 path に POST を並列で発行する汎用ヘルパー。
    Agent が down していても timeout 内に諦め、全体の完了を妨げない。"""
    agents = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if not agents:
        return []
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    headers = {"X-Agent-Token": token} if token else {}

    async def _post_one(agent: dict) -> dict:
        agent_id = agent.get("id", agent["host"])
        url = f"http://{agent['host']}:{agent['port']}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers)
                data = resp.json() if resp.content else {}
            log.info("Agent %s POST %s OK", agent_id, path)
            return {"id": agent_id, "name": agent.get("name"), "success": True, **data}
        except Exception as e:
            log.warning("Agent %s POST %s failed: %s", agent_id, path, e)
            return {"id": agent_id, "name": agent.get("name"), "success": False, "error": str(e)}

    return await asyncio.gather(*[_post_one(a) for a in agents._agents])


async def update_all_agents(bot) -> list[dict]:
    """全Windows Agentに /update を並列で呼んでコード更新させる。"""
    return await post_all_agents(bot, "/update", timeout=8)


async def get_all_agent_versions(bot) -> list[dict]:
    """全 Windows Agent の /version を並列取得してハッシュを返す。"""
    agents = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if not agents:
        return []
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    headers = {"X-Agent-Token": token} if token else {}

    async def _get_one(agent: dict) -> dict:
        agent_id = agent.get("id", agent["host"])
        url = f"http://{agent['host']}:{agent['port']}/version"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url, headers=headers)
                data = resp.json() if resp.content else {}
            full = (data.get("version") or "").strip()
            return {
                "id": agent_id,
                "name": agent.get("name"),
                "alive": True,
                "version": full[:7] if full else "",
                "version_full": full,
            }
        except Exception as e:
            return {
                "id": agent_id,
                "name": agent.get("name"),
                "alive": False,
                "version": "",
                "error": str(e),
            }

    return await asyncio.gather(*[_get_one(a) for a in agents._agents])


def find_agent_by_id(bot, agent_id: str) -> dict | None:
    pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if not pool:
        return None
    for a in pool._agents:
        if str(a.get("id", a.get("host"))) == str(agent_id):
            return a
    return None


async def agent_request(bot, method: str, path: str, role: str | None = None) -> list[dict]:
    """Windows Agent にリクエストを送る。role指定があればそのAgentのみ。"""
    agents_pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if not agents_pool:
        return []
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    headers = {"X-Agent-Token": token} if token else {}
    results = []
    for agent in agents_pool._agents:
        if role and agent.get("role") != role:
            continue
        base = {
            "agent": agent.get("id", agent["host"]),
            "agent_id": agent.get("id"),
            "agent_name": agent.get("name"),
            "role": agent.get("role", "unknown"),
            "host": agent.get("host"),
            "port": agent.get("port"),
        }
        url = f"http://{agent['host']}:{agent['port']}{path}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers)
                data = resp.json()
            results.append({**base, "alive": True, **data})
        except Exception as e:
            results.append({**base, "alive": False, "error": str(e)})
    return results


async def agent_request_json(bot, method: str, path: str, role: str | None = None, json_body: dict | None = None) -> list[dict]:
    """Windows Agent にJSONボディ付きリクエストを送る。"""
    agents_pool = getattr(getattr(bot, "unit_manager", None), "agent_pool", None)
    if not agents_pool:
        return []
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    headers = {"X-Agent-Token": token} if token else {}
    results = []
    for agent in agents_pool._agents:
        if role and agent.get("role") != role:
            continue
        url = f"http://{agent['host']}:{agent['port']}{path}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers, json=json_body)
                data = resp.json()
            results.append({"agent": agent.get("id", agent["host"]), "role": agent.get("role", "unknown"), **data})
        except Exception as e:
            results.append({"agent": agent.get("id", agent["host"]), "role": agent.get("role", "unknown"), "error": str(e)})
    return results
