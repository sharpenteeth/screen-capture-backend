from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import (
    OPEN_REQUEST,
    get_current_user,
    is_online,
    monitored_users,
    require_roles,
    validate_password,
    validate_role,
    validate_username,
)
from app.models import Assignment, Device, ImageRequest, Screenshot, User
from app.routers.screenshots import user_screenshots
from app.schemas import (
    AssignmentOut,
    CaptureSettings,
    CreateAssignment,
    CreateUser,
    DashboardOut,
    PersonOut,
    UpdateUser,
    UserOut,
)
from app.seed import ensure_capture_interval, set_capture_interval
from app.security import hash_password
from app.timeutil import iso_z, utcnow

router = APIRouter(prefix="/api", tags=["directory"])
router.add_api_route("/users/{user_id}/screenshots", user_screenshots, methods=["GET"])


def user_out(user: User) -> dict:
    return UserOut(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
    ).model_dump(by_alias=True)


@router.get("/settings")
def get_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    del user
    return CaptureSettings(capture_interval_minutes=ensure_capture_interval(db)).model_dump(by_alias=True)


@router.put("/settings")
def update_settings(
    body: CaptureSettings,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    del user
    minutes = set_capture_interval(db, body.capture_interval_minutes)
    return CaptureSettings(capture_interval_minutes=minutes).model_dump(by_alias=True)


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    settings = request.app.state.settings
    people = monitored_users(db, user)
    ids = [person.id for person in people]
    since = utcnow() - timedelta(hours=24)
    cards = []
    for person in people:
        device = db.scalar(select(Device).where(Device.user_id == person.id).order_by(Device.last_seen.desc()))
        shot = db.scalar(
            select(Screenshot).where(Screenshot.user_id == person.id).order_by(Screenshot.capture_time.desc())
        )
        cards.append(
            PersonOut(
                user_id=person.id,
                full_name=person.full_name,
                username=person.username,
                role=person.role,
                online=bool(device and is_online(device.last_seen, settings.online_seconds)),
                hostname=device.hostname if device else None,
                device_id=device.device_key if device else None,
                last_capture=iso_z(shot.capture_time) if shot else None,
                last_thumbnail_url=f"/api/screenshots/{shot.id}/thumbnail" if shot else None,
            )
        )
    online_devices = 0
    captures = 0
    open_requests = 0
    if ids:
        devices = db.scalars(select(Device).where(Device.user_id.in_(ids))).all()
        online_devices = sum(1 for device in devices if is_online(device.last_seen, settings.online_seconds))
        captures = db.scalar(
            select(func.count())
            .select_from(Screenshot)
            .where(Screenshot.user_id.in_(ids), Screenshot.capture_time >= since)
        ) or 0
        open_requests = db.scalar(
            select(func.count())
            .select_from(ImageRequest)
            .where(ImageRequest.user_id.in_(ids), ImageRequest.status.in_(tuple(OPEN_REQUEST)))
        ) or 0
    payload = DashboardOut(
        online_devices=online_devices,
        recent_captures=int(captures),
        open_requests=int(open_requests),
        people=cards,
    )
    return payload.model_dump(by_alias=True)


@router.get("/users")
def list_users(user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(User).order_by(User.full_name)).all()
    return [user_out(row) for row in rows]


@router.post("/users", status_code=201)
def create_user(
    body: CreateUser,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    del user
    username = validate_username(body.username)
    password = validate_password(body.password)
    role = validate_role(body.role)
    full_name = body.full_name.strip()
    if not full_name or len(full_name) > 128:
        raise HTTPException(status_code=400, detail="Invalid name")
    if db.scalar(select(User).where(User.username == username)) is not None:
        raise HTTPException(status_code=409, detail="Username already exists")
    created = User(username=username, password_hash=hash_password(password), full_name=full_name, role=role)
    db.add(created)
    db.commit()
    db.refresh(created)
    return user_out(created)


def _active_admin_count(db: Session) -> int:
    return int(
        db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))) or 0
    )


def _clear_role_assignments(db: Session, user: User, new_role: str) -> None:
    if user.role == "manager" and new_role != "manager":
        for row in db.scalars(select(Assignment).where(Assignment.manager_id == user.id)).all():
            db.delete(row)
    if user.role == "employee" and new_role != "employee":
        for row in db.scalars(select(Assignment).where(Assignment.employee_id == user.id)).all():
            db.delete(row)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUser,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.full_name is not None:
        full_name = body.full_name.strip()
        if not full_name or len(full_name) > 128:
            raise HTTPException(status_code=400, detail="Invalid name")
        target.full_name = full_name
    if body.username is not None:
        username = validate_username(body.username)
        taken = db.scalar(select(User).where(User.username == username, User.id != target.id))
        if taken is not None:
            raise HTTPException(status_code=409, detail="Username already exists")
        target.username = username
    if body.password:
        target.password_hash = hash_password(validate_password(body.password))
    next_role = validate_role(body.role) if body.role is not None else target.role
    next_active = target.is_active if body.is_active is None else body.is_active
    loses_admin = target.role == "admin" and target.is_active and (next_role != "admin" or not next_active)
    if loses_admin and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Keep at least one active admin")
    if actor.id == target.id and not next_active:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    if next_role != target.role:
        _clear_role_assignments(db, target, next_role)
        target.role = next_role
    target.is_active = next_active
    db.commit()
    db.refresh(target)
    return user_out(target)


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    actor: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if actor.id == target.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if target.role == "admin" and target.is_active and _active_admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Keep at least one active admin")
    has_history = (
        db.scalar(select(Screenshot.id).where(Screenshot.user_id == target.id).limit(1)) is not None
        or db.scalar(select(Device.id).where(Device.user_id == target.id).limit(1)) is not None
        or db.scalar(
            select(ImageRequest.id)
            .where(or_(ImageRequest.user_id == target.id, ImageRequest.requester_id == target.id))
            .limit(1)
        )
        is not None
    )
    if has_history:
        raise HTTPException(status_code=409, detail="Disable this account. It still has screenshots or devices.")
    for row in db.scalars(
        select(Assignment).where(or_(Assignment.manager_id == target.id, Assignment.employee_id == target.id))
    ).all():
        db.delete(row)
    db.delete(target)
    db.commit()


@router.get("/assignments")
def list_assignments(user: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> list[dict]:
    del user
    rows = db.scalars(select(Assignment).order_by(Assignment.id)).all()
    results = []
    for row in rows:
        manager = db.get(User, row.manager_id)
        employee = db.get(User, row.employee_id)
        results.append(
            AssignmentOut(
                id=row.id,
                manager_id=row.manager_id,
                employee_id=row.employee_id,
                manager_name=manager.full_name if manager else "",
                employee_name=employee.full_name if employee else "",
            ).model_dump(by_alias=True)
        )
    return results


@router.post("/assignments", status_code=201)
def create_assignment(
    body: CreateAssignment,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict:
    del user
    manager = db.get(User, body.manager_id)
    employee = db.get(User, body.employee_id)
    if manager is None or employee is None:
        raise HTTPException(status_code=404, detail="User not found")
    if manager.role != "manager" or employee.role != "employee":
        raise HTTPException(status_code=400, detail="Assignments link a manager to an employee")
    if manager.id == employee.id:
        raise HTTPException(status_code=400, detail="Invalid assignment")
    row = Assignment(manager_id=manager.id, employee_id=employee.id)
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Assignment already exists") from exc
    db.refresh(row)
    return AssignmentOut(
        id=row.id,
        manager_id=manager.id,
        employee_id=employee.id,
        manager_name=manager.full_name,
        employee_name=employee.full_name,
    ).model_dump(by_alias=True)


@router.delete("/assignments/{assignment_id}", status_code=204)
def delete_assignment(
    assignment_id: int,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    del user
    row = db.get(Assignment, assignment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Assignment not found")
    db.delete(row)
    db.commit()
