from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import Settings
from app.models import User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user: User, settings: Settings) -> str:
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.token_hours)
    payload = {"sub": str(user.id), "role": user.role, "exp": expires}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_user_id(token: str, secret_key: str) -> int:
    payload = jwt.decode(token, secret_key, algorithms=["HS256"])
    return int(payload["sub"])
