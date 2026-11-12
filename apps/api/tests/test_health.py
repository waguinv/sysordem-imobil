from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_root():
    response = client.get("/api/v1")
    assert response.status_code == 200
    assert response.json()["phase"] == "2c"


def test_protected_route_requires_token():
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/api/v1/members", headers={"Authorization": "Bearer lixo"}).status_code == 401
