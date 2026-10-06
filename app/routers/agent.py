from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import AGENT_STATUS, OPEN_REQUEST, get_current_user, validate_file_id
from app.messaging import request_message
from app.models import Device, ImageRequest, User
from app.schemas import AgentStatusUpdate, RegisterDevice
from app.storage import Storage
from app.timeutil import parse_time, utcnow

router = APIRouter(prefix="/api/agent", tags=["agent"])

PACKAGE_LIMIT = 200 * 1024 * 1024
IMAGE_LIMIT = 8 * 1024 * 1024


async def read_limited(upload: UploadFile, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail="Upload too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/register")
def register_device(
    body: RegisterDevice,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not body.device_key or len(body.device_key) > 64 or any(c.isspace() for c in body.device_key):
        raise HTTPException(status_code=400, detail="Invalid device id")
    device = db.scalar(select(Device).where(Device.device_key == body.device_key))
    if device is not None and device.user_id != user.id:
        raise HTTPException(status_code=409, detail="Device is registered to another user")
    if device is None:
        device = Device(device_key=body.device_key, user_id=user.id)
        db.add(device)
    device.hostname = (body.hostname or "")[:128]
    device.agent_version = (body.agent_version or "")[:32]
    device.last_seen = utcnow()
    db.commit()
    db.refresh(device)
    return {
        "id": device.id,
        "deviceId": device.device_key,
        "hostname": device.hostname,
        "userId": device.user_id,
    }


@router.get("/pending-requests")
def pending_requests(
    request: Request,
    device_id: str | None = Query(default=None, alias="deviceId"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    storage: Storage = request.app.state.storage
    rows = db.scalars(
        select(ImageRequest)
        .where(ImageRequest.user_id == user.id, ImageRequest.status.in_(tuple(OPEN_REQUEST)))
        .order_by(ImageRequest.id)
    ).all()
    messages = []
    for row in rows:
        message = request_message(db, storage, row)
        if device_id and message["deviceId"] != device_id:
            continue
        messages.append(message)
    return {"requests": messages}


@router.post("/requests/{request_id}/status")
def update_request_status(
    request_id: int,
    body: AgentStatusUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if body.status not in AGENT_STATUS:
        raise HTTPException(status_code=400, detail="Invalid status")
    row = db.get(ImageRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if row.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    if row.status != "ready":
        row.status = body.status
        row.error = (body.error or "")[:500] or None
        row.updated_at = utcnow()
        db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/packages")
async def upload_package(
    request: Request,
    request_id: int = Form(..., alias="requestId"),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    import io
    import json
    import zipfile

    from app.crypto import decrypt_bytes
    from app.models import FullImage
    from app.schemas import PackageMeta

    row = db.get(ImageRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if row.user_id != user.id and user.role != "admin":
        raise HTTPException(status_code=403, detail="Not allowed")
    if row.status == "ready":
        return {"id": row.id, "status": row.status, "images": 0}

    storage: Storage = request.app.state.storage
    blob = await read_limited(file, PACKAGE_LIMIT)
    try:
        package_key = storage.unwrap_key(row.package_key)
        raw = decrypt_bytes(blob, package_key)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            meta = PackageMeta.model_validate(json.loads(archive.read("metadata.json")))
            if meta.request_id != row.id:
                raise HTTPException(status_code=400, detail="Package request id does not match")
            if len(meta.images) > 400:
                raise HTTPException(status_code=400, detail="Package has too many images")
            extracted: list[tuple] = []
            total = 0
            for item in meta.images:
                validate_file_id(item.client_file_id)
                captured = parse_time(item.capture_time)
                if captured < row.start_time or captured > row.end_time:
                    raise HTTPException(status_code=400, detail="Image is outside the requested range")
                info = archive.getinfo(f"{item.client_file_id}.jpg")
                if info.file_size > IMAGE_LIMIT:
                    raise HTTPException(status_code=400, detail="Image inside package is too large")
                total += info.file_size
                if total > PACKAGE_LIMIT:
                    raise HTTPException(status_code=413, detail="Package too large")
                extracted.append((item, archive.read(info)))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not read encrypted package") from exc

    previous = db.scalars(select(FullImage).where(FullImage.request_id == row.id)).all()
    for image in previous:
        storage.remove(image.file_path)
        db.delete(image)
    db.flush()

    for item, jpeg in extracted:
        relative = storage.save_full(row.id, item.client_file_id, jpeg)
        db.add(
            FullImage(
                request_id=row.id,
                capture_time=parse_time(item.capture_time),
                client_file_id=item.client_file_id,
                file_path=relative,
                width=item.width,
                height=item.height,
            )
        )
    row.status = "ready"
    row.error = None
    row.updated_at = utcnow()
    db.commit()
    return {"id": row.id, "status": row.status, "images": len(extracted)}
