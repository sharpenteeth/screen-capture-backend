import asyncio
from typing import Any

from fastapi import WebSocket


class Hub:
    def __init__(self) -> None:
        self.agents: dict[int, set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self.lock:
            self.agents.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self.lock:
            sockets = self.agents.get(user_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self.agents.pop(user_id, None)

    async def send_user(self, user_id: int, message: dict[str, Any]) -> int:
        async with self.lock:
            sockets = list(self.agents.get(user_id, set()))
        delivered = 0
        dead: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(message)
                delivered += 1
            except Exception:
                dead.append(websocket)
        for websocket in dead:
            await self.disconnect(user_id, websocket)
        return delivered
