import base64

from sqlalchemy.orm import Session

from app.models import Device, ImageRequest
from app.storage import Storage
from app.timeutil import iso_z


def request_message(db: Session, storage: Storage, row: ImageRequest) -> dict:
    device = db.get(Device, row.device_id)
    package_key = base64.b64encode(storage.unwrap_key(row.package_key)).decode("ascii")
    return {
        "type": "full_image_request",
        "requestId": row.id,
        "deviceId": device.device_key if device else "",
        "startTime": iso_z(row.start_time),
        "endTime": iso_z(row.end_time),
        "packageKey": package_key,
    }
