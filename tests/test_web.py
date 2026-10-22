import pytest

from radar import config
from radar.web import create_app


@pytest.fixture
def local_client(database):
    return create_app().test_client()


@pytest.fixture
def server(database, monkeypatch):
    monkeypatch.setattr(config, "SERVER", True)
    monkeypatch.setattr(config, "MIN_SCHEDULE_MIN", 15)
    monkeypatch.setattr(config, "SIGNUP_CODE", "invite")
    monkeypatch.setattr(config, "CRON_SECRET", "cron")
    app = create_app()
    return app.test_client, app


def test_local_mode_needs_no_login_but_rejects_other_hosts(local_client):
    assert local_client.get("/api/state").status_code == 200
    assert local_client.post("/api/settings", json={"deal": "sale"}).get_json()["deal"] == "sale"
    assert local_client.post("/api/settings", data="deal=sale").status_code == 415      # forms can't change state
    assert local_client.get("/api/state", headers={"Host": "evil.test"}).status_code == 403


def test_server_mode_accounts(server):
    make_client, _ = server
    a, b = make_client(), make_client()
    assert a.get("/").status_code == 302 and a.get("/api/state").status_code == 401
    assert a.post("/api/auth/signup", json={"email": "a@x.in", "password": "password1"}).status_code == 403
    assert a.post("/api/auth/signup", json={"email": "a@x.in", "password": "password1", "code": "invite"}).status_code == 200
    assert b.post("/api/auth/signup", json={"email": "b@x.in", "password": "password1", "code": "invite"}).status_code == 200
    a.post("/api/settings", json={"alerts": {"telegram_token": "123:secret"}, "schedule_min": 5})
    state = a.get("/api/state").get_json()
    assert state["settings"]["alerts"]["telegram_token"] != "123:secret" and state["settings"]["schedule_min"] == 15
    building = a.post("/api/buildings", json={"name": "Mantri Classic", "lat": 12.93, "lon": 77.63}).get_json()
    assert b.get("/api/state").get_json()["buildings"] == []
    assert b.delete(f"/api/buildings/{building['id']}", json={}).get_json()["deleted"] is False
    a.post("/api/auth/logout", json={})
    assert a.get("/api/state").status_code == 401
    assert a.post("/api/auth/login", json={"email": "a@x.in", "password": "nope"}).status_code == 401
    assert a.post("/api/auth/login", json={"email": "A@x.in", "password": "password1"}).status_code == 200


def test_cron_needs_its_secret(server):
    make_client, _ = server
    c = make_client()
    assert c.post("/api/cron").status_code == 403
    assert c.post("/api/cron", headers={"X-Cron-Secret": "cron"}).get_json() == {"started": 0}
    assert c.get("/healthz").get_json()["ok"] is True
