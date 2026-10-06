import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import ensure_can_view, get_current_user, monitored_users
from app.messaging import request_message
from app.models import Device, FullImage, ImageRequest, User
from app.schemas import CreateImageRequest, FullImageOut, ImageRequestOut
from app.storage import Storage
from app.timeutil import iso_z, parse_time, utcnow
from app.ws import Hub

router = APIRouter(tags=["requests"])


def ensure_investigator(user: User) -> None:
    if user.role not in {"admin", "manager"}:
        raise HTTPException(status_code=403, detail="Not allowed")


def load_request(db: Session, user: User, request_id: int) -> ImageRequest:
    ensure_investigator(user)
    row = db.get(ImageRequest, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    ensure_can_view(db, user, row.user_id)
    return row


def serialize_request(db: Session, row: ImageRequest) -> dict:
    requester = db.get(User, row.requester_id)
    target = db.get(User, row.user_id)
    device = db.get(Device, row.device_id)
    images = db.scalars(select(FullImage).where(FullImage.request_id == row.id).order_by(FullImage.capture_time)).all()
    payload = ImageRequestOut(
        id=row.id,
        requester_id=row.requester_id,
        requester_name=requester.full_name if requester else "",
        user_id=row.user_id,
        user_name=target.full_name if target else "",
        device_id=device.device_key if device else "",
        start_time=iso_z(row.start_time),
        end_time=iso_z(row.end_time),
        status=row.status,
        error=row.error,
        created_at=iso_z(row.created_at),
        images=[
            FullImageOut(
                id=image.id,
                capture_time=iso_z(image.capture_time),
                client_file_id=image.client_file_id,
                width=image.width,
                height=image.height,
                url=f"/api/requests/{row.id}/images/{image.id}",
            )
            for image in images
        ],
    )
    return payload.model_dump(by_alias=True)


@router.post("/api/screenshots/request")
async def create_request(
    body: CreateImageRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ensure_investigator(user)
    ensure_can_view(db, user, body.user_id)
    start = parse_time(body.start_time)
    end = parse_time(body.end_time)
    if start > end:
        raise HTTPException(status_code=400, detail="startTime must be before endTime")
    if end - start > timedelta(hours=24):
        raise HTTPException(status_code=400, detail="Range cannot exceed 24 hours")
    target = db.get(User, body.user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    device = db.scalar(select(Device).where(Device.device_key == body.device_id, Device.user_id == target.id))
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    storage: Storage = request.app.state.storage
    row = ImageRequest(
        requester_id=user.id,
        user_id=target.id,
        device_id=device.id,
        start_time=start,
        end_time=end,
        status="pending",
        package_key=storage.wrap_key(os.urandom(32)),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    hub: Hub = request.app.state.hub
    await hub.send_user(target.id, request_message(db, storage, row))
    return serialize_request(db, row)


@router.get("/api/requests")
def list_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    ensure_investigator(user)
    stmt = select(ImageRequest).order_by(ImageRequest.created_at.desc()).limit(50)
    if user.role != "admin":
        allowed = [person.id for person in monitored_users(db, user)]
        if not allowed:
            return []
        stmt = stmt.where(ImageRequest.user_id.in_(allowed))
    return [serialize_request(db, row) for row in db.scalars(stmt).all()]


@router.get("/api/requests/{request_id}")
def get_request(request_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return serialize_request(db, load_request(db, user, request_id))


@router.get("/api/requests/{request_id}/images/{image_id}")
def download_image(
    request_id: int,
    image_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = load_request(db, user, request_id)
    image = db.get(FullImage, image_id)
    if image is None or image.request_id != row.id:
        raise HTTPException(status_code=404, detail="Image not found")
    storage: Storage = request.app.state.storage
    return Response(content=storage.read_full(image.file_path), media_type="image/jpeg")


@router.get("/api/requests/{request_id}/package")
def download_package(
    request_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    import io
    import json
    import zipfile

    row = load_request(db, user, request_id)
    if row.status != "ready":
        raise HTTPException(status_code=409, detail="Package is not ready")
    images = db.scalars(select(FullImage).where(FullImage.request_id == row.id).order_by(FullImage.capture_time)).all()
    storage: Storage = request.app.state.storage
    buffer = io.BytesIO()
    meta = []
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for image in images:
            archive.writestr(f"{image.client_file_id}.jpg", storage.read_full(image.file_path))
            meta.append(
                {
                    "clientFileId": image.client_file_id,
                    "captureTime": iso_z(image.capture_time),
                    "width": image.width,
                    "height": image.height,
                }
            )
        archive.writestr("metadata.json", json.dumps({"requestId": row.id, "images": meta}))
    filename = f"screenshots-{row.id}.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
