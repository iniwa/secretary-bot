"""処理フロー追跡モジュール — SSEでリアルタイム配信。"""

import asyncio
import time
import uuid

from src.logger import get_logger

log = get_logger(__name__)

# グローバルシングルトン
_instance: "FlowTracker | None" = None


def get_flow_tracker() -> "FlowTracker":
    global _instance
    if _instance is None:
        _instance = FlowTracker()
    return _instance


class FlowTracker:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._current_flow: dict | None = None
        self._last_flow: dict | None = None
        self._lock = asyncio.Lock()

    async def start_flow(self) -> str:
        """新しい処理フローを開始し、flow_idを返す。"""
        flow_id = uuid.uuid4().hex[:12]
        async with self._lock:
            self._current_flow = {
                "flow_id": flow_id,
                "started_at": time.time(),
                "nodes": {},
            }
        await self._broadcast({
            "type": "flow_start",
            "flow_id": flow_id,
            "timestamp": time.time(),
        })
        return flow_id

    async def emit(self, node: str, status: str, detail: dict | None = None, flow_id: str | None = None) -> None:
        """ノード状態変更イベントを発火。"""
        now = time.time()
        event = {
            "type": "node_update",
            "flow_id": flow_id,
            "node": node,
            "status": status,
            "detail": detail or {},
            "timestamp": now,
        }
        async with self._lock:
            if self._current_flow:
                if flow_id is None or flow_id == self._current_flow.get("flow_id"):
                    event["flow_id"] = self._current_flow["flow_id"]
                    node_data = self._current_flow["nodes"].get(node, {})
                    node_data["status"] = status
                    node_data["detail"] = detail or {}
                    node_data["updated_at"] = now
                    if status == "active" and "started_at" not in node_data:
                        node_data["started_at"] = now
                    if status in ("done", "error", "skipped") and "started_at" in node_data:
                        node_data["duration_ms"] = int((now - node_data["started_at"]) * 1000)
                    self._current_flow["nodes"][node] = node_data
        await self._broadcast(event)

    async def end_flow(self, flow_id: str | None = None) -> None:
        """処理フローを完了。"""
        fid = flow_id
        duration_ms = 0
        async with self._lock:
            if self._current_flow:
                duration_ms = int((time.time() - self._current_flow["started_at"]) * 1000)
                fid = flow_id or self._current_flow["flow_id"]
                self._last_flow = dict(self._current_flow)
                self._last_flow["duration_ms"] = duration_ms
                self._current_flow = None
        if fid is None:
            return
        await self._broadcast({
            "type": "flow_end",
            "flow_id": fid,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        })

    def get_state(self) -> dict:
        """現在 or 最後のフロー状態を返す。"""
        if self._current_flow:
            return {"active": True, "flow": self._current_flow}
        if self._last_flow:
            return {"active": False, "flow": self._last_flow}
        return {"active": False, "flow": None}

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    async def _broadcast(self, event: dict) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)
