import re
from datetime import datetime, timedelta

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Assignment, User
from app.security import decode_user_id
from app.timeutil import utcnow

FILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
USERNAME = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
ROLES = {"admin", "manager", "employee"}
AGENT_STATUS = {"accepted", "uploading", "unavailable", "failed"}
OPEN_REQUEST = {"pending", "accepted", "uploading"}


def get_current_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    settings = request.app.state.settings
    try:
        user_id = decode_user_id(token, settings.secret_key)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Not allowed")
        return user

    return checker


def assigned_employee_ids(db: Session, manager_id: int) -> list[int]:
    rows = db.scalars(select(Assignment.employee_id).where(Assignment.manager_id == manager_id)).all()
    return list(rows)


def can_view(db: Session, actor: User, user_id: int) -> bool:
    if actor.role == "admin":
        return True
    if actor.role == "employee":
        return actor.id == user_id
    if actor.role == "manager":
        return user_id in assigned_employee_ids(db, actor.id)
    return False


def ensure_can_view(db: Session, actor: User, user_id: int) -> None:
    if not can_view(db, actor, user_id):
        raise HTTPException(status_code=403, detail="Not allowed to view this user")


def monitored_users(db: Session, actor: User) -> list[User]:
    if actor.role == "admin":
        return list(
            db.scalars(
                select(User).where(User.role == "employee", User.is_active.is_(True)).order_by(User.full_name)
            ).all()
        )
    if actor.role == "manager":
        ids = assigned_employee_ids(db, actor.id)
        if not ids:
            return []
        return list(
            db.scalars(
                select(User).where(User.id.in_(ids), User.is_active.is_(True)).order_by(User.full_name)
            ).all()
        )
    return [actor]


def validate_file_id(value: str) -> str:
    if not FILE_ID.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid file id")
    return value


def validate_username(value: str) -> str:
    if not USERNAME.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid username")
    return value


def validate_password(value: str) -> str:
    if len(value) < 8 or len(value.encode("utf-8")) > 72:
        raise HTTPException(status_code=400, detail="Password must be 8 to 72 bytes")
    return value


def validate_role(value: str) -> str:
    if value not in ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    return value


def is_online(last_seen: datetime | None, window_seconds: int) -> bool:
    if last_seen is None:
        return False
    return utcnow() - last_seen <= timedelta(seconds=window_seconds)
