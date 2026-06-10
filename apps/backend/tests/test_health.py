from doktok_api.main import create_app
from doktok_core.config import Settings
from fastapi.testclient import TestClient


def test_health_returns_ok() -> None:
    app = create_app(settings=Settings(env="test"))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "doktok-ng-backend"
    assert body["environment"] == "test"
    assert "version" in body
