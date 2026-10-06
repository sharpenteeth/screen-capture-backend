from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class APIModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CaptureSettings(APIModel):
    capture_interval_minutes: int = Field(ge=1, le=60)


class LoginRequest(APIModel):
    username: str
    password: str


class UserOut(APIModel):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool = True


class TokenResponse(APIModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class RegisterDevice(APIModel):
    device_key: str = Field(min_length=1, max_length=64)
    hostname: str = ""
    agent_version: str = ""


class DeviceOut(APIModel):
    id: int
    device_key: str
    hostname: str
    user_id: int
    last_seen: str | None = None
    online: bool = False


class ThumbnailMeta(APIModel):
    device_id: str
    capture_time: str
    client_file_id: str
    width: int = 0
    height: int = 0
    duplicate_of: str | None = None
    content_hash: str | None = None
    user_id: int | None = None


class ScreenshotOut(APIModel):
    id: int
    user_id: int
    device_id: str
    device_hostname: str
    capture_time: str
    client_file_id: str
    status: str
    duplicate: bool
    width: int
    height: int
    thumbnail_bytes: int
    thumbnail_url: str


class CreateUser(APIModel):
    username: str
    password: str
    full_name: str
    role: str


class UpdateUser(APIModel):
    username: str | None = None
    password: str | None = None
    full_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class CreateAssignment(APIModel):
    manager_id: int
    employee_id: int


class AssignmentOut(APIModel):
    id: int
    manager_id: int
    employee_id: int
    manager_name: str
    employee_name: str


class PersonOut(APIModel):
    user_id: int
    full_name: str
    username: str
    role: str
    online: bool
    hostname: str | None = None
    device_id: str | None = None
    last_capture: str | None = None
    last_thumbnail_url: str | None = None
    activity_state: str | None = None
    activity_app: str | None = None
    activity_title: str | None = None
    idle_seconds: int | None = None
    active_seconds: int = 0
    away_seconds: int = 0


class ActivitySample(APIModel):
    recorded_at: str
    app_name: str = ""
    window_title: str = ""
    idle_seconds: int = 0
    state: str = "active"


class ActivityBatch(APIModel):
    device_id: str
    events: list[ActivitySample]


class ActivityOut(APIModel):
    id: int
    recorded_at: str
    app_name: str
    window_title: str
    idle_seconds: int
    state: str


class DashboardOut(APIModel):
    online_devices: int
    recent_captures: int
    open_requests: int
    people: list[PersonOut]


class CreateImageRequest(APIModel):
    user_id: int
    device_id: str
    start_time: str
    end_time: str


class FullImageOut(APIModel):
    id: int
    capture_time: str
    client_file_id: str
    width: int
    height: int
    url: str


class ImageRequestOut(APIModel):
    id: int
    requester_id: int
    requester_name: str
    user_id: int
    user_name: str
    device_id: str
    start_time: str
    end_time: str
    status: str
    error: str | None = None
    created_at: str
    images: list[FullImageOut] = []


class AgentStatusUpdate(APIModel):
    status: str
    error: str | None = None


class PackageImageMeta(APIModel):
    client_file_id: str
    capture_time: str
    width: int = 0
    height: int = 0
    duplicate_of: str | None = None


class PackageMeta(APIModel):
    request_id: int
    images: list[PackageImageMeta]
