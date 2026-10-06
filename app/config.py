import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    secret_key: str
    database_url: str
    data_dir: Path
    token_hours: int = 12
    online_seconds: int = 60

    @classmethod
    def load(cls) -> "Settings":
        backend_dir = Path(__file__).resolve().parents[1]
        _load_env_file(backend_dir / ".env")
        default_dir = backend_dir / "data"
        data_dir = Path(os.environ.get("DATA_DIR", str(default_dir)))
        data_dir.mkdir(parents=True, exist_ok=True)

        secret = os.environ.get("SECRET_KEY", "").strip()
        secret_path = data_dir / "jwt.secret"
        if not secret:
            if secret_path.exists():
                secret = secret_path.read_text(encoding="utf-8").strip()
            else:
                secret = secrets.token_urlsafe(32)
                secret_path.write_text(secret, encoding="utf-8")

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            database_url = "sqlite:///" + (data_dir / "app.db").as_posix()

        return cls(secret_key=secret, database_url=database_url, data_dir=data_dir)


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
