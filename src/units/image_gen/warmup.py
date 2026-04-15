"""モデルキャッシュのウォームアップ/同期共通ロジック。

Dispatcher（起動時）と model_sync ユニット（定期ポーリング）の両方から利用される。
best-effort: 失敗はログに残すだけで上位処理を止めない。
"""

from __future__ import annotations

from typing import Any

from src.logger import get_logger
from src.units.image_gen.agent_client import AgentClient

log = get_logger(__name__)


async def warmup_agent(
    bot,
    agent: dict[str, Any],
    *,
    default_ckpt: str | None,
    trigger_sync: bool,
    client: AgentClient | None = None,
) -> dict[str, Any]:
    """1 エージェント分のウォームアップ。

    - `/capability` を取得
    - `model_cache_manifest` を洗い替え
    - `default_ckpt` が未キャッシュかつ comfyui_available なら `/cache/sync` 発射
      （`trigger_sync=False` の場合は同期は行わず状態同期のみ）

    戻り値: {"agent_id": ..., "capability": dict|None,
             "missing_default": bool, "sync_id": str|None, "error": str|None}
    """
    agent_id = agent.get("id", "")
    result: dict[str, Any] = {
        "agent_id": agent_id, "capability": None,
        "missing_default": False, "sync_id": None, "error": None,
    }
    ac = client or AgentClient(agent)
    own_client = client is None
    try:
        try:
            cap = await ac.capability()
        except Exception as e:
            log.info("warmup: capability fetch failed for %s: %s", agent_id, e)
            result["error"] = f"capability_failed: {e}"
            return result
        result["capability"] = cap

        models_by_type = _cap_to_manifest(cap)
        try:
            await sync_manifest(bot, agent_id, models_by_type)
        except Exception as e:
            log.warning("warmup: manifest sync failed for %s: %s", agent_id, e)

        if not default_ckpt:
            return result
        has_default = default_ckpt in models_by_type["checkpoints"]
        result["missing_default"] = not has_default
        if has_default:
            return result
        if not trigger_sync:
            log.info("warmup: %s missing %s, sync skipped (trigger_sync=False)",
                     agent_id, default_ckpt)
            return result
        if not cap.get("comfyui_available"):
            log.info("warmup: %s comfyui not available, skip pre-sync", agent_id)
            return result
        try:
            resp = await ac.cache_sync(
                [{"type": "checkpoints", "filename": default_ckpt}],
                reason="warmup",
            )
            sid = resp.get("sync_id", "")
            result["sync_id"] = sid
            log.info("warmup: %s cache_sync queued sync_id=%s", agent_id, sid)
        except Exception as e:
            log.warning("warmup: %s cache_sync failed: %s", agent_id, e)
            result["error"] = f"cache_sync_failed: {e}"
        return result
    finally:
        if own_client:
            try:
                await ac.close()
            except Exception:
                pass


def _cap_to_manifest(cap: dict[str, Any]) -> dict[str, list[str]]:
    def _names(key: str) -> list[str]:
        return [m.get("filename", "") for m in cap.get(key, []) if m.get("filename")]
    return {
        "checkpoints": _names("models"),
        "loras": _names("loras"),
        "vae": _names("vaes"),
        "embeddings": _names("embeddings"),
        "upscale_models": _names("upscale_models"),
    }


async def sync_manifest(
    bot, agent_id: str, by_type: dict[str, list[str]],
) -> None:
    """capability の結果で model_cache_manifest を洗い替え（agent_id 単位）。"""
    rows = await bot.database.fetchall(
        "SELECT file_type, filename FROM model_cache_manifest WHERE agent_id = ?",
        (agent_id,),
    )
    have = {(r["file_type"], r["filename"]) for r in rows}
    seen: set[tuple[str, str]] = set()
    for t, names in by_type.items():
        for fn in names:
            key = (t, fn)
            seen.add(key)
            if key in have:
                continue
            await bot.database.execute(
                "INSERT OR REPLACE INTO model_cache_manifest "
                "(agent_id, file_type, filename, last_used_at) "
                "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
                (agent_id, t, fn),
            )
    for t, fn in have - seen:
        await bot.database.execute(
            "DELETE FROM model_cache_manifest "
            "WHERE agent_id = ? AND file_type = ? AND filename = ?",
            (agent_id, t, fn),
        )


async def warmup_all_agents(
    bot, *, trigger_sync: bool,
) -> list[dict[str, Any]]:
    """config の windows_agents 全て対してウォームアップを実行。"""
    ig_cfg = (bot.config.get("units") or {}).get("image_gen") or {}
    default_ckpt = ig_cfg.get("default_base_model")
    agents = list(getattr(bot.unit_manager.agent_pool, "_agents", []))
    if not agents:
        log.info("warmup: no agents configured, skip")
        return []
    results: list[dict[str, Any]] = []
    for agent in agents:
        try:
            r = await warmup_agent(
                bot, agent,
                default_ckpt=default_ckpt, trigger_sync=trigger_sync,
            )
            results.append(r)
        except Exception as e:
            log.warning("warmup: agent %s failed: %s", agent.get("id", "?"), e)
            results.append({
                "agent_id": agent.get("id", ""),
                "error": str(e),
            })
    return results
