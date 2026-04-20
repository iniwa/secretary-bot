"""ChromaDB 記憶閲覧 API: /api/memory/*。"""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from src.web._context import WebContext

_MEMORY_COLLECTIONS = ("ai_memory", "people_memory", "conversation_log")


def register(app: FastAPI, ctx: WebContext) -> None:
    bot = ctx.bot

    @app.get("/api/memory/{collection}", dependencies=[Depends(ctx.verify)])
    async def get_memory(collection: str, limit: int = 200, offset: int = 0):
        if collection not in _MEMORY_COLLECTIONS:
            raise HTTPException(400, f"unknown collection: {collection}")
        # 全件取得して新しい順にソート（ChromaDBはソート非対応のため）
        all_items = bot.chroma.get_all(collection, limit=10000, offset=0)
        total = len(all_items)
        # created_at があればそれでソート、なければ逆順（新しいもの優先）
        has_dates = any((it.get("metadata") or {}).get("created_at") for it in all_items)
        if has_dates:
            all_items.sort(key=lambda x: (x.get("metadata") or {}).get("created_at", ""), reverse=True)
        else:
            all_items.reverse()
        items = all_items[offset:offset + limit]
        # Resolve user_id → display name for people_memory
        if collection == "people_memory":
            for item in items:
                uid = (item.get("metadata") or {}).get("user_id")
                if uid:
                    try:
                        user = bot.get_user(int(uid))
                        if user:
                            item["metadata"]["user_name"] = user.display_name
                    except (ValueError, TypeError):
                        pass
        return {"items": items, "total": total}

    @app.get("/api/memory/{collection}/search", dependencies=[Depends(ctx.verify)])
    async def search_memory(collection: str, q: str = "", n: int = 20):
        """セマンティック検索。"""
        if collection not in _MEMORY_COLLECTIONS:
            raise HTTPException(400, f"unknown collection: {collection}")
        if not q.strip():
            raise HTTPException(400, "query parameter 'q' is required")
        col = bot.chroma.get_collection(collection)
        try:
            raw = col.query(query_texts=[q.strip()], n_results=n, include=["documents", "metadatas", "distances"])
            items = []
            ids = raw.get("ids", [[]])[0]
            docs = raw.get("documents", [[]])[0]
            metas = raw.get("metadatas", [[]])[0]
            dists = raw.get("distances", [[]])[0]
            for i, doc_id in enumerate(ids):
                item = {
                    "id": doc_id,
                    "text": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                    "distance": dists[i] if i < len(dists) else None,
                }
                items.append(item)
            # Resolve user names for people_memory
            if collection == "people_memory":
                for item in items:
                    uid = (item.get("metadata") or {}).get("user_id")
                    if uid:
                        try:
                            user = bot.get_user(int(uid))
                            if user:
                                item["metadata"]["user_name"] = user.display_name
                        except (ValueError, TypeError):
                            pass
            return {"items": items, "total": len(items)}
        except Exception as e:
            raise HTTPException(500, str(e))

    @app.delete("/api/memory/{collection}/{doc_id}", dependencies=[Depends(ctx.verify)])
    async def delete_memory(collection: str, doc_id: str):
        if collection not in _MEMORY_COLLECTIONS:
            raise HTTPException(400, f"unknown collection: {collection}")
        bot.chroma.delete(collection, doc_id)
        return {"ok": True}
