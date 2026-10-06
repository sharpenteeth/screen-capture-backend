from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ensure_can_view, get_current_user
from app.models import ActivityEvent, Device, User
from app.schemas import ActivityBatch, ActivityOut
from app.timeutil import iso_z, parse_time, utcnow

router = APIRouter(prefix="/api", tags=["activity"])

SPAN_CAP_SECONDS = 6 * 60


def summarize_activity(events: list[ActivityEvent], now: datetime) -> tuple[int, int]:
    ordered = sorted(events, key=lambda row: (row.recorded_at, row.id))
    active = 0
    idle = 0
    for index, event in enumerate(ordered):
        start = event.recorded_at
        if start > now:
            continue
        end = ordered[index + 1].recorded_at if index + 1 < len(ordered) else now
        if end < start:
            continue
        span = min(int((end - start).total_seconds()), SPAN_CAP_SECONDS)
        if event.state == "idle":
            idle += span
        else:
            active += span
    return active, idle


def list_activity(db: Session, user_id: int, start: str | None, end: str | None, limit: int) -> list[dict]:
    stmt = select(ActivityEvent).where(ActivityEvent.user_id == user_id)
    if start:
        stmt = stmt.where(ActivityEvent.recorded_at >= parse_time(start))
    if end:
        stmt = stmt.where(ActivityEvent.recorded_at < parse_time(end))
    stmt = stmt.order_by(ActivityEvent.recorded_at, ActivityEvent.id).limit(min(max(limit, 1), 1000))
    return [
        ActivityOut(
            id=row.id,
            recorded_at=iso_z(row.recorded_at),
            app_name=row.app_name,
            window_title=row.window_title,
            idle_seconds=row.idle_seconds,
            state=row.state,
        ).model_dump(by_alias=True)
        for row in db.scalars(stmt).all()
    ]


@router.post("/activity")
def upload_activity(
    body: ActivityBatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not body.events or len(body.events) > 200:
        raise HTTPException(status_code=400, detail="Send between 1 and 200 activity events")
    device = db.scalar(select(Device).where(Device.device_key == body.device_id))
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail="Device is not registered")
    device.last_seen = utcnow()
    saved = 0
    for event in body.events:
        state = event.state if event.state in {"active", "idle"} else "active"
        db.add(
            ActivityEvent(
                user_id=user.id,
                device_id=device.id,
                recorded_at=parse_time(event.recorded_at),
                app_name=(event.app_name or "")[:128],
                window_title=(event.window_title or "")[:200],
                idle_seconds=max(0, min(int(event.idle_seconds), 24 * 3600)),
                state=state,
            )
        )
        saved += 1
    db.commit()
    return {"saved": saved}


@router.get("/users/{user_id}/activity")
def user_activity(
    user_id: int,
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    limit: int = 500,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    ensure_can_view(db, actor, target.id)
    return list_activity(db, target.id, start, end, limit)
