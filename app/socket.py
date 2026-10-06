from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app import database
from app.messaging import request_message
from app.models import Device, ImageRequest, User
from app.security import decode_user_id
from app.deps import AGENT_STATUS, OPEN_REQUEST
from app.timeutil import utcnow


async def agent_socket(websocket: WebSocket) -> None:
    if database.SessionLocal is None:
        await websocket.close(code=1011)
        return
    db = database.SessionLocal()
    connected = False
    user_id = None
    try:
        token = websocket.query_params.get("token", "")
        settings = websocket.app.state.settings
        try:
            user_id = decode_user_id(token, settings.secret_key)
        except Exception:
            await websocket.close(code=4401)
            return
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            await websocket.close(code=4401)
            return
        hub = websocket.app.state.hub
        storage = websocket.app.state.storage
        await hub.connect(user.id, websocket)
        connected = True
        rows = db.scalars(
            select(ImageRequest)
            .where(ImageRequest.user_id == user.id, ImageRequest.status.in_(tuple(OPEN_REQUEST)))
            .order_by(ImageRequest.id)
        ).all()
        await websocket.send_json(
            {"type": "pending_requests", "requests": [request_message(db, storage, row) for row in rows]}
        )
        while True:
            message = await websocket.receive_json()
            kind = message.get("type")
            if kind == "heartbeat":
                device_key = str(message.get("deviceId", ""))
                device = db.scalar(
                    select(Device).where(Device.device_key == device_key, Device.user_id == user.id)
                )
                if device is not None:
                    device.last_seen = utcnow()
                    db.commit()
            elif kind == "request_status":
                status = str(message.get("status", ""))
                if status not in AGENT_STATUS:
                    continue
                try:
                    request_id = int(message.get("requestId", 0))
                except (TypeError, ValueError):
                    continue
                row = db.get(ImageRequest, request_id)
                if row is None or (row.user_id != user.id and user.role != "admin"):
                    continue
                if row.status != "ready":
                    row.status = status
                    row.error = str(message.get("error") or "")[:500] or None
                    row.updated_at = utcnow()
                    db.commit()
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        if connected and user_id is not None:
            await websocket.app.state.hub.disconnect(user_id, websocket)
        db.close()
