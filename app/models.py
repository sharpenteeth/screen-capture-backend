from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.timeutil import utcnow


class Base(DeclarativeBase):
    pass


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("manager_id", "employee_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    manager_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    hostname: Mapped[str] = mapped_column(String(128), default="")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    agent_version: Mapped[str] = mapped_column(String(32), default="")


class Screenshot(Base):
    __tablename__ = "screenshots"
    __table_args__ = (UniqueConstraint("user_id", "client_file_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    capture_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    client_file_id: Mapped[str] = mapped_column(String(80), nullable=False)
    thumbnail_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumbnail_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True, nullable=True)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("screenshots.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="available")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ImageRequest(Base):
    __tablename__ = "image_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime)
    end_time: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
    package_key: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FullImage(Base):
    __tablename__ = "full_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("image_requests.id"), index=True)
    capture_time: Mapped[datetime] = mapped_column(DateTime)
    client_file_id: Mapped[str] = mapped_column(String(80))
    file_path: Mapped[str] = mapped_column(String(512), default="")
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True, nullable=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
