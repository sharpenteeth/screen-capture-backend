import base64
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.crypto import encrypt_bytes

JPEG = b"\xff\xd8\xff\xd9"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-with-enough-length")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "app.db").as_posix())
    from app.main import create_app

    application = create_app()
    with TestClient(application) as client:
        yield client


def login(client: TestClient, username: str) -> tuple[dict[str, str], dict]:
    passwords = {"admin": "admin123", "manager": "manager123"}
    response = client.post(
        "/api/login",
        json={"username": username, "password": passwords.get(username, "employee123")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['accessToken']}"}, body["user"]


def test_health(api: TestClient) -> None:
    assert api.get("/api/health").json()["ok"] is True


def test_admin_sets_capture_interval(api: TestClient) -> None:
    admin_headers, _admin = login(api, "admin")
    manager_headers, _manager = login(api, "manager")
    john_headers, _john = login(api, "john")

    current = api.get("/api/settings", headers=john_headers)
    assert current.status_code == 200, current.text
    assert current.json()["captureIntervalMinutes"] == 20

    denied = api.put("/api/settings", json={"captureIntervalMinutes": 15}, headers=manager_headers)
    assert denied.status_code == 403

    updated = api.put("/api/settings", json={"captureIntervalMinutes": 15}, headers=admin_headers)
    assert updated.status_code == 200, updated.text
    assert updated.json()["captureIntervalMinutes"] == 15
    assert api.get("/api/settings", headers=john_headers).json()["captureIntervalMinutes"] == 15

    invalid = api.put("/api/settings", json={"captureIntervalMinutes": 0}, headers=admin_headers)
    assert invalid.status_code == 422


def test_admin_manages_users(api: TestClient) -> None:
    admin_headers, admin = login(api, "admin")
    manager_headers, _manager = login(api, "manager")
    created = api.post(
        "/api/users",
        json={"username": "casey", "password": "casey1234", "fullName": "Casey Ortiz", "role": "manager"},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    john_id = next(user["id"] for user in api.get("/api/users", headers=admin_headers).json() if user["username"] == "john")
    assigned = api.post(
        "/api/assignments",
        json={"managerId": user_id, "employeeId": john_id},
        headers=admin_headers,
    )
    assert assigned.status_code == 201, assigned.text

    updated = api.patch(
        f"/api/users/{user_id}",
        json={"fullName": "Casey O", "role": "employee", "password": "newpass123"},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["fullName"] == "Casey O"
    assert updated.json()["role"] == "employee"
    links = api.get("/api/assignments", headers=admin_headers).json()
    assert all(row["managerId"] != user_id and row["employeeId"] != user_id for row in links)
    assert api.post("/api/login", json={"username": "casey", "password": "newpass123"}).status_code == 200

    denied = api.patch(f"/api/users/{user_id}", json={"isActive": False}, headers=manager_headers)
    assert denied.status_code == 403
    disabled = api.patch(f"/api/users/{user_id}", json={"isActive": False}, headers=admin_headers)
    assert disabled.status_code == 200, disabled.text
    assert api.post("/api/login", json={"username": "casey", "password": "newpass123"}).status_code == 401
    assert api.patch(f"/api/users/{admin['id']}", json={"isActive": False}, headers=admin_headers).status_code == 400

    removed = api.delete(f"/api/users/{user_id}", headers=admin_headers)
    assert removed.status_code == 204, removed.text
    assert all(user["id"] != user_id for user in api.get("/api/users", headers=admin_headers).json())


def test_invalid_login(api: TestClient) -> None:
    response = api.post("/api/login", json={"username": "john", "password": "wrong-password"})
    assert response.status_code == 401


def test_monitoring_flow(api: TestClient) -> None:
    john_headers, john = login(api, "john")
    manager_headers, _manager = login(api, "manager")
    admin_headers, _admin = login(api, "admin")
    _sara_headers, sara = login(api, "sara")

    assert api.get("/api/users", headers=admin_headers).status_code == 200
    assert api.get("/api/users", headers=manager_headers).status_code == 403

    registered = api.post(
        "/api/agent/register",
        json={"deviceKey": "pc-john", "hostname": "JOHN-PC"},
        headers=john_headers,
    )
    assert registered.status_code == 200, registered.text

    uploaded = api.post(
        "/api/screenshots/thumbnail",
        data={
            "metadata": json.dumps(
                {
                    "deviceId": "pc-john",
                    "captureTime": "2026-10-05T14:00:00Z",
                    "clientFileId": "img0001",
                    "width": 1920,
                    "height": 1080,
                }
            )
        },
        files={"file": ("thumb.jpg", JPEG, "image/jpeg")},
        headers=john_headers,
    )
    assert uploaded.status_code == 200, uploaded.text

    duplicate = api.post(
        "/api/screenshots/thumbnail",
        data={
            "metadata": json.dumps(
                {
                    "deviceId": "pc-john",
                    "captureTime": "2026-10-05T14:05:00Z",
                    "clientFileId": "img0002",
                    "width": 1920,
                    "height": 1080,
                    "duplicateOf": "img0001",
                }
            )
        },
        headers=john_headers,
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["duplicate"] is True

    shots = api.get(
        f"/api/users/{john['id']}/screenshots",
        params={"from": "2026-10-05T00:00:00Z", "to": "2026-10-06T00:00:00Z"},
        headers=manager_headers,
    )
    assert shots.status_code == 200, shots.text
    body = shots.json()
    assert len(body) == 2
    assert api.get(f"/api/users/{sara['id']}/screenshots", headers=manager_headers).status_code == 403
    assert api.get(body[1]["thumbnailUrl"], headers=manager_headers).content == JPEG

    denied = api.post(
        "/api/screenshots/request",
        json={
            "userId": john["id"],
            "deviceId": "pc-john",
            "startTime": "2026-10-05T14:00:00Z",
            "endTime": "2026-10-05T14:05:00Z",
        },
        headers=john_headers,
    )
    assert denied.status_code == 403
    outside = api.post(
        "/api/screenshots/request",
        json={
            "userId": sara["id"],
            "deviceId": "missing",
            "startTime": "2026-10-05T14:00:00Z",
            "endTime": "2026-10-05T14:05:00Z",
        },
        headers=manager_headers,
    )
    assert outside.status_code == 403

    created = api.post(
        "/api/screenshots/request",
        json={
            "userId": john["id"],
            "deviceId": "pc-john",
            "startTime": "2026-10-05T14:00:00Z",
            "endTime": "2026-10-05T14:05:00Z",
        },
        headers=manager_headers,
    )
    assert created.status_code == 200, created.text
    request_id = created.json()["id"]

    pending = api.get("/api/agent/pending-requests", params={"deviceId": "pc-john"}, headers=john_headers)
    assert pending.status_code == 200, pending.text
    package_key = base64.b64decode(pending.json()["requests"][0]["packageKey"])
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "metadata.json",
            json.dumps(
                {
                    "requestId": request_id,
                    "images": [
                        {
                            "clientFileId": "img0001",
                            "captureTime": "2026-10-05T14:00:00Z",
                            "width": 1920,
                            "height": 1080,
                        },
                        {
                            "clientFileId": "img0002",
                            "captureTime": "2026-10-05T14:05:00Z",
                            "width": 1920,
                            "height": 1080,
                            "duplicateOf": "img0001",
                        },
                    ],
                }
            ),
        )
        bundle.writestr("img0001.jpg", JPEG)
    uploaded_package = api.post(
        "/api/agent/packages",
        data={"requestId": str(request_id)},
        files={"file": ("package.enc", encrypt_bytes(archive.getvalue(), package_key), "application/octet-stream")},
        headers=john_headers,
    )
    assert uploaded_package.status_code == 200, uploaded_package.text
    assert uploaded_package.json()["images"] == 2

    detail = api.get(f"/api/requests/{request_id}", headers=manager_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "ready"
    assert api.get(detail.json()["images"][0]["url"], headers=manager_headers).content == JPEG
    assert api.get(detail.json()["images"][1]["url"], headers=manager_headers).content == JPEG
    package = api.get(f"/api/requests/{request_id}/package", headers=manager_headers)
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as downloaded:
        assert downloaded.read("img0001.jpg") == JPEG

    overview = api.get("/api/dashboard", headers=manager_headers).json()
    names = [person["fullName"] for person in overview["people"]]
    assert names == ["John Smith"]
    assert overview["recentCaptures"] == 2


def test_websocket_receives_pending_request(api: TestClient) -> None:
    john_headers, john = login(api, "john")
    manager_headers, _manager = login(api, "manager")
    assert (
        api.post(
            "/api/agent/register",
            json={"deviceKey": "pc-john", "hostname": "JOHN-PC"},
            headers=john_headers,
        ).status_code
        == 200
    )
    created = api.post(
        "/api/screenshots/request",
        json={
            "userId": john["id"],
            "deviceId": "pc-john",
            "startTime": "2026-10-05T15:00:00Z",
            "endTime": "2026-10-05T15:00:00Z",
        },
        headers=manager_headers,
    )
    assert created.status_code == 200, created.text
    token = john_headers["Authorization"].split(" ", 1)[1]
    with api.websocket_connect(f"/ws/agent?token={token}") as socket:
        message = socket.receive_json()
        assert message["type"] == "pending_requests"
        assert message["requests"][0]["requestId"] == created.json()["id"]
        socket.send_json({"type": "heartbeat", "deviceId": "pc-john"})
