"""ChromaDB操作（インプロセス・PersistentClient）。"""

import uuid
from datetime import datetime

import chromadb
from chromadb.config import Settings

from src.database import JST
from src.logger import get_logger

log = get_logger(__name__)


def _now_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


class ChromaMemory:
    def __init__(self, path: str = "/app/data/chromadb"):
        self._client = chromadb.PersistentClient(
            path=path,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collections: dict[str, chromadb.Collection] = {}
        log.info("ChromaDB initialized at %s", path)

    def get_collection(self, name: str) -> chromadb.Collection:
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(name)
        return self._collections[name]

    def add(self, collection_name: str, doc_id: str, text: str, metadata: dict | None = None) -> None:
        """エントリを追加。鮮度メタデータ(saved_at/hit_count/last_accessed_at)を自動付与する。"""
        col = self.get_collection(collection_name)
        meta = dict(metadata) if metadata else {}
        now = _now_str()
        meta.setdefault("saved_at", now)
        # ChromaDBの数値型はfloat/int両方受け付ける
        if "hit_count" not in meta:
            meta["hit_count"] = 0
        meta.setdefault("last_accessed_at", meta.get("saved_at", now))
        col.upsert(ids=[doc_id], documents=[text], metadatas=[meta])

    def search(
        self,
        collection_name: str,
        query: str,
        n_results: int = 5,
        where: dict | None = None,
        update_access: bool = True,
    ) -> list[dict]:
        """類似検索。update_access=Trueならヒットした各ドキュメントのhit_count/last_accessed_atを更新。"""
        col = self.get_collection(collection_name)
        try:
            kwargs = {"query_texts": [query], "n_results": n_results}
            if where:
                kwargs["where"] = where
            results = col.query(**kwargs)
        except Exception as e:
            log.warning("ChromaDB search failed on '%s': %s", collection_name, e)
            return []

        items: list[dict] = []
        if results and results.get("documents"):
            ids = results.get("ids", [[]])[0]
            docs = results["documents"][0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            for i, doc in enumerate(docs):
                items.append({
                    "id": ids[i] if i < len(ids) else None,
                    "text": doc,
                    "metadata": metas[i] if i < len(metas) else {},
                    "distance": distances[i] if i < len(distances) else None,
                })

        if update_access and items:
            self._bump_access(col, items)
        return items

    def _bump_access(self, col: chromadb.Collection, items: list[dict]) -> None:
        """ヒットしたドキュメントの hit_count/last_accessed_at を更新する。"""
        try:
            now = _now_str()
            ids: list[str] = []
            metas: list[dict] = []
            for it in items:
                doc_id = it.get("id")
                if not doc_id:
                    continue
                meta = dict(it.get("metadata") or {})
                meta["hit_count"] = int(meta.get("hit_count", 0) or 0) + 1
                meta["last_accessed_at"] = now
                ids.append(doc_id)
                metas.append(meta)
                # 返り値側の metadata も同期（呼び出し元が見る場合に備えて）
                it["metadata"] = meta
            if ids:
                col.update(ids=ids, metadatas=metas)
        except Exception as e:
            log.debug("ChromaDB access bump failed: %s", e)

    def add_with_dedup(
        self,
        collection_name: str,
        text: str,
        metadata: dict | None = None,
        skip_threshold: float = 0.92,
        merge_threshold: float = 0.80,
    ) -> str:
        """意味的重複チェック付きの追加。
        - 類似度 >= skip_threshold: 既存のhit_count/last_accessed_atだけ更新して "skipped"
        - 類似度 >= merge_threshold: 既存を削除して新規add（鮮度更新） → "merged"
        - それ以下: 通常add → "added"
        類似度 = 1 - cosine_distance
        """
        col = self.get_collection(collection_name)
        # 既存の中に類似があるかチェック（access更新はしない。ヒットに応じて手動制御）
        try:
            existing = self.search(collection_name, text, n_results=1, update_access=False)
        except Exception as e:
            log.debug("dedup search failed on '%s': %s (fallback to plain add)", collection_name, e)
            existing = []

        now = _now_str()
        if existing:
            top = existing[0]
            distance = top.get("distance")
            if distance is not None:
                similarity = 1.0 - float(distance)
                hit_id = top.get("id")
                if similarity >= skip_threshold and hit_id:
                    # skip: 既存のhit_count/last_accessed_atだけ更新
                    try:
                        meta = dict(top.get("metadata") or {})
                        meta["hit_count"] = int(meta.get("hit_count", 0) or 0) + 1
                        meta["last_accessed_at"] = now
                        col.update(ids=[hit_id], metadatas=[meta])
                    except Exception as e:
                        log.debug("skip-update failed: %s", e)
                    log.info(
                        "dedup skip on '%s' (sim=%.3f): %.60s",
                        collection_name, similarity, text,
                    )
                    return "skipped"
                if similarity >= merge_threshold and hit_id:
                    # merge: 既存を削除して新規add（鮮度更新）
                    try:
                        col.delete(ids=[hit_id])
                    except Exception as e:
                        log.debug("merge-delete failed: %s", e)
                    doc_id = uuid.uuid4().hex[:16]
                    self.add(collection_name, doc_id, text, metadata)
                    log.info(
                        "dedup merge on '%s' (sim=%.3f): %.60s",
                        collection_name, similarity, text,
                    )
                    return "merged"

        # 通常追加
        doc_id = uuid.uuid4().hex[:16]
        self.add(collection_name, doc_id, text, metadata)
        return "added"

    def count(self, collection_name: str) -> int:
        return self.get_collection(collection_name).count()

    def get_all(self, collection_name: str, limit: int = 200, offset: int = 0) -> list[dict]:
        """コレクションの全エントリを返す。"""
        col = self.get_collection(collection_name)
        try:
            results = col.get(limit=limit, offset=offset, include=["documents", "metadatas"])
        except Exception as e:
            log.warning("ChromaDB get_all failed on '%s': %s", collection_name, e)
            return []
        items = []
        ids = results.get("ids", [])
        docs = results.get("documents", [])
        metas = results.get("metadatas", [])
        for i, doc_id in enumerate(ids):
            items.append({
                "id": doc_id,
                "text": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
            })
        return items

    def delete(self, collection_name: str, doc_id: str) -> None:
        """指定IDのエントリを削除する。"""
        col = self.get_collection(collection_name)
        col.delete(ids=[doc_id])
