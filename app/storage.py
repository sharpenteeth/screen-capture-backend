import base64
import os
from pathlib import Path

from app.crypto import decrypt_bytes, encrypt_bytes


class Storage:
    def __init__(self, root: Path, master_key: bytes) -> None:
        self.root = root
        self.master_key = master_key
        (root / "thumbnails").mkdir(parents=True, exist_ok=True)
        (root / "full").mkdir(parents=True, exist_ok=True)

    @classmethod
    def open(cls, root: Path) -> "Storage":
        key_path = root / "master.key"
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            key = os.urandom(32)
            key_path.write_bytes(key)
        if len(key) != 32:
            raise RuntimeError("Server master key must be 32 bytes")
        return cls(root, key)

    def path_for(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("Path escapes the storage directory")
        return path

    def save_thumbnail(self, user_id: int, client_file_id: str, data: bytes) -> str:
        relative = Path("thumbnails") / str(user_id) / f"{client_file_id}.jpg"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return relative.as_posix()

    def read_thumbnail(self, relative: str) -> bytes:
        return self.path_for(relative).read_bytes()

    def save_full(self, request_id: int, client_file_id: str, jpeg: bytes) -> str:
        relative = Path("full") / str(request_id) / f"{client_file_id}.enc"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(encrypt_bytes(jpeg, self.master_key))
        return relative.as_posix()

    def read_full(self, relative: str) -> bytes:
        return decrypt_bytes(self.path_for(relative).read_bytes(), self.master_key)

    def remove(self, relative: str) -> None:
        path = self.path_for(relative)
        if path.exists():
            path.unlink()

    def wrap_key(self, key: bytes) -> str:
        return base64.b64encode(encrypt_bytes(key, self.master_key)).decode("ascii")

    def unwrap_key(self, wrapped: str) -> bytes:
        return decrypt_bytes(base64.b64decode(wrapped), self.master_key)
