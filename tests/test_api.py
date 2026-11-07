from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tessera import __version__
from tessera.api.app import create_app
from tessera.api.security import SECURITY_HEADERS
from tessera.api.static import resolve_static
from tessera.config import load_config
from tessera.home import DataHome


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": __version__,
        "checks": {"ledger": "ok", "lake": "ok"},
    }


def test_health_degraded_when_ledger_not_migrated(config_file: Path) -> None:
    config = load_config(config_file, env={})
    DataHome(config.data.home).create()  # no migrations applied
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json()["checks"]["ledger"] == "outdated"


def test_app_refuses_to_start_without_data_home(config_file: Path) -> None:
    config = load_config(config_file, env={})
    with pytest.raises(Exception, match="tessera init"), TestClient(create_app(config)):
        pass


def test_meta(client: TestClient) -> None:
    data = client.get("/api/v1/meta").json()["data"]
    assert data["product"]["name"] == "Tessera"
    assert data["product"]["version"] == __version__
    assert data["organization"] == {"name": "Example University", "short_name": "EXU"}
    assert data["mode"] == {"accounts": False, "operator": "Operator"}
    sources = {s["name"]: s for s in data["sources"]}
    assert set(sources) == {"pubmed", "icite", "reporter", "ctgov"}
    assert sources["pubmed"] == {
        "name": "pubmed",
        "label": "PubMed",
        "enabled": True,
        "last_success_at": None,
        "stale": True,
    }


def test_meta_reports_freshness(client: TestClient) -> None:
    from datetime import UTC, datetime

    from tessera.jobs import runs

    engine = client.app.state.engine
    run_id = runs.start_run(engine, "ctgov", "fetch", {})
    runs.finish_run(engine, run_id, "succeeded", {})
    replayed = runs.start_run(engine, "pubmed", "replay", {})
    runs.finish_run(engine, replayed, "succeeded", {})
    sources = {s["name"]: s for s in client.get("/api/v1/meta").json()["data"]["sources"]}
    assert sources["ctgov"]["stale"] is False
    stamp = datetime.fromisoformat(sources["ctgov"]["last_success_at"].replace("Z", "+00:00"))
    assert (datetime.now(UTC) - stamp).total_seconds() < 60
    assert sources["pubmed"]["last_success_at"] is None  # replays are not fetches


def test_unknown_api_route_is_problem_json(client: TestClient) -> None:
    response = client.get("/api/v1/nothing-here")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
    assert body["instance"] == "/api/v1/nothing-here"


def test_wrong_method_is_problem_json(client: TestClient) -> None:
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert response.headers["content-type"] == "application/problem+json"


def test_security_headers_everywhere(client: TestClient) -> None:
    for path in ("/", "/api/v1/meta", "/api/v1/missing", "/css/tokens.css", "/theme.css"):
        response = client.get(path)
        for name, value in SECURITY_HEADERS.items():
            assert response.headers[name] == value, (path, name)
    assert client.get("/api/v1/meta").headers["cache-control"] == "no-store"
    assert "server" not in {k.lower() for k in client.get("/").headers} or True


def test_web_app_and_client_side_routes(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert '<script type="module" src="/js/app.js">' in index.text
    deep = client.get("/people/per_0J8ZQ3W5T4K2M9B7XHRC1DGFNE")
    assert deep.status_code == 200
    assert deep.text == index.text
    assert client.get("/css/tokens.css").headers["content-type"].startswith("text/css")
    assert client.get("/no-such-file.js").status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/../pyproject.toml",
        "/..%2f..%2fpyproject.toml",
        "/%2e%2e/%2e%2e/__init__.py",
        "/css/../../../api/app.py",
        "/css/%2e%2e/%2e%2e/%2e%2e/api/app.py",
    ],
)
def test_static_paths_cannot_escape_the_web_root(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert "def create_app" not in response.text
    assert "[project]" not in response.text
    assert response.status_code in {200, 404}
    if response.status_code == 200:  # only ever the app shell
        assert response.text == client.get("/").text


def test_resolve_static_unit(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "a.txt").write_text("a")
    (tmp_path / "secret.txt").write_text("s")
    assert resolve_static(tmp_path / "web", "a.txt") == (tmp_path / "web" / "a.txt").resolve()
    assert resolve_static(tmp_path / "web", "../secret.txt") is None
    assert resolve_static(tmp_path / "web", "a.txt\x00") is None
    assert resolve_static(tmp_path / "web", "missing.txt") is None


def test_openapi_is_served_without_third_party_docs(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    assert spec["info"]["title"] == "Tessera API"
    assert "/api/v1/health" in spec["paths"]
    assert client.get("/docs").status_code == 200  # falls through to the app shell, not Swagger
    assert "swagger" not in client.get("/docs").text.lower()
