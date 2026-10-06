from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, SystemSetting, User
from app.security import hash_password

CAPTURE_INTERVAL_KEY = "capture_interval_minutes"
DEFAULT_CAPTURE_INTERVAL_MINUTES = 20

DEMO_USERS = (
    ("admin", "admin123", "Avery Chen", "admin"),
    ("manager", "manager123", "Morgan Lee", "manager"),
    ("john", "employee123", "John Smith", "employee"),
    ("sara", "employee123", "Sara Nguyen", "employee"),
)


def ensure_capture_interval(db: Session) -> int:
    row = db.get(SystemSetting, CAPTURE_INTERVAL_KEY)
    if row is None:
        row = SystemSetting(key=CAPTURE_INTERVAL_KEY, value=str(DEFAULT_CAPTURE_INTERVAL_MINUTES))
        db.add(row)
        db.commit()
        db.refresh(row)
    try:
        minutes = int(row.value)
    except ValueError:
        minutes = DEFAULT_CAPTURE_INTERVAL_MINUTES
    return minutes


def set_capture_interval(db: Session, minutes: int) -> int:
    row = db.get(SystemSetting, CAPTURE_INTERVAL_KEY)
    if row is None:
        row = SystemSetting(key=CAPTURE_INTERVAL_KEY, value=str(minutes))
        db.add(row)
    else:
        row.value = str(minutes)
    db.commit()
    return minutes


def seed(db: Session) -> None:
    ensure_capture_interval(db)
    if db.scalar(select(User.id).limit(1)) is not None:
        return
    users: dict[str, User] = {}
    for username, password, full_name, role in DEMO_USERS:
        user = User(
            username=username,
            password_hash=hash_password(password),
            full_name=full_name,
            role=role,
        )
        db.add(user)
        users[username] = user
    db.flush()
    db.add(Assignment(manager_id=users["manager"].id, employee_id=users["john"].id))
    db.commit()
