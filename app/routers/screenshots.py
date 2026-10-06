from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ensure_can_view, get_current_user, validate_file_id
from app.models import Device, Screenshot, User
from app.routers.agent import read_limited
from app.schemas import ScreenshotOut, ThumbnailMeta
from app.storage import Storage
from app.timeutil import iso_z, parse_time, utcnow

router = APIRouter(prefix="/api/screenshots", tags=["screenshots"])

THUMBNAIL_LIMIT = 512 * 1024


def resolve_thumbnail(db: Session, shot: Screenshot) -> Screenshot:
    seen: set[int] = set()
    current = shot
    while current.duplicate_of_id and current.id not in seen:
        seen.add(current.id)
        parent = db.get(Screenshot, current.duplicate_of_id)
        if parent is None:
            break
        current = parent
    return current


def to_screenshot(db: Session, shot: Screenshot) -> dict:
    device = db.get(Device, shot.device_id)
    payload = ScreenshotOut(
        id=shot.id,
        user_id=shot.user_id,
        device_id=device.device_key if device else "",
        device_hostname=device.hostname if device else "",
        capture_time=iso_z(shot.capture_time),
        client_file_id=shot.client_file_id,
        status=shot.status,
        duplicate=shot.duplicate_of_id is not None,
        width=shot.width,
        height=shot.height,
        thumbnail_bytes=shot.thumbnail_bytes,
        thumbnail_url=f"/api/screenshots/{shot.id}/thumbnail",
    )
    return payload.model_dump(by_alias=True)


def list_for_user(
    db: Session,
    user_id: int,
    start: str | None,
    end: str | None,
    limit: int,
) -> list[dict]:
    stmt = select(Screenshot).where(Screenshot.user_id == user_id)
    if start:
        stmt = stmt.where(Screenshot.capture_time >= parse_time(start))
    if end:
        stmt = stmt.where(Screenshot.capture_time < parse_time(end))
    stmt = stmt.order_by(Screenshot.capture_time).limit(min(max(limit, 1), 1000))
    return [to_screenshot(db, shot) for shot in db.scalars(stmt).all()]


@router.post("/thumbnail")
async def upload_thumbnail(
    request: Request,
    metadata: str = Form(...),
    file: UploadFile | None = File(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        meta = ThumbnailMeta.model_validate_json(metadata)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid metadata") from exc
    if meta.user_id is not None and meta.user_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot upload for another user")
    client_file_id = validate_file_id(meta.client_file_id)
    device = db.scalar(select(Device).where(Device.device_key == meta.device_id))
    if device is None or device.user_id != user.id:
        raise HTTPException(status_code=404, detail="Device is not registered")
    device.last_seen = utcnow()

    existing = db.scalar(
        select(Screenshot).where(Screenshot.user_id == user.id, Screenshot.client_file_id == client_file_id)
    )
    if existing is not None:
        db.commit()
        return {"id": existing.id, "status": existing.status, "duplicate": existing.duplicate_of_id is not None}

    parent = None
    payload = b""
    if meta.duplicate_of:
        validate_file_id(meta.duplicate_of)
        parent = db.scalar(
            select(Screenshot).where(
                Screenshot.user_id == user.id,
                Screenshot.client_file_id == meta.duplicate_of,
            )
        )
        if parent is None:
            raise HTTPException(status_code=409, detail="Original screenshot is not uploaded yet")
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="Thumbnail file is required")
        payload = await read_limited(file, THUMBNAIL_LIMIT)
        if not payload:
            raise HTTPException(status_code=400, detail="Thumbnail file is empty")

    storage: Storage = request.app.state.storage
    relative = storage.save_thumbnail(user.id, client_file_id, payload) if payload else None
    shot = Screenshot(
        user_id=user.id,
        device_id=device.id,
        capture_time=parse_time(meta.capture_time),
        client_file_id=client_file_id,
        thumbnail_path=relative,
        duplicate_of_id=parent.id if parent else None,
        status="duplicate" if parent else "available",
        width=meta.width,
        height=meta.height,
        thumbnail_bytes=len(payload),
        content_hash=(meta.content_hash or "")[:64] or None,
    )
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return {"id": shot.id, "status": shot.status, "duplicate": parent is not None}


@router.get("/{screenshot_id}/thumbnail")
def download_thumbnail(
    screenshot_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    shot = db.get(Screenshot, screenshot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="Screenshot not found")
    ensure_can_view(db, user, shot.user_id)
    resolved = resolve_thumbnail(db, shot)
    if not resolved.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    storage: Storage = request.app.state.storage
    data = storage.read_thumbnail(resolved.thumbnail_path)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


def user_screenshots(
    user_id: int,
    start: str | None = Query(default=None, alias="from"),
    end: str | None = Query(default=None, alias="to"),
    limit: int = 300,
    actor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    ensure_can_view(db, actor, target.id)
    return list_for_user(db, target.id, start, end, limit)
