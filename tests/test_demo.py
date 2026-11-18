"""Verify delivery of the browser demo through the local API."""

from fastapi.testclient import TestClient

from app.api import app


def test_demo_serves_upload_and_lookup_without_creating_a_database(tmp_path, monkeypatch):
    db_path = tmp_path / "demo.sqlite"
    monkeypatch.setenv("SCREEN_DB_PATH", str(db_path))

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'type="file" name="files" multiple' in response.text
    assert 'name="batch_id"' in response.text
    assert "Mock" in response.text
    assert "textContent" in response.text
    for private_value in ("innerHTML", str(db_path), "SCREEN_DB_PATH", ".venv", "E:\\"):
        assert private_value not in response.text
    assert not db_path.exists()
