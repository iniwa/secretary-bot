"""ChromaDB操作（インプロセス・PersistentClient）。"""

import chromadb

from src.logger import get_logger

log = get_logger(__name__)


class ChromaMemory:
    def __init__(self, path: str = "/app/data/chromadb"):
        self._client = chromadb.PersistentClient(path=path)
        self._collections: dict[str, chromadb.Collection] = {}
        log.info("ChromaDB initialized at %s", path)

    def get_collection(self, name: str) -> chromadb.Collection:
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(name)
        return self._collections[name]

    def add(self, collection_name: str, doc_id: str, text: str, metadata: dict | None = None) -> None:
        col = self.get_collection(collection_name)
        col.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def search(self, collection_name: str, query: str, n_results: int = 5) -> list[dict]:
        col = self.get_collection(collection_name)
        try:
            results = col.query(query_texts=[query], n_results=n_results)
        except Exception:
            return []

        items = []
        if results and results.get("documents"):
            docs = results["documents"][0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            for i, doc in enumerate(docs):
                items.append({
                    "text": doc,
                    "metadata": metas[i] if i < len(metas) else {},
                    "distance": distances[i] if i < len(distances) else None,
                })
        return items

    def count(self, collection_name: str) -> int:
        return self.get_collection(collection_name).count()
