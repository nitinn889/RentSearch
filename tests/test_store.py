import pytest

from radar import store
from radar.models import Listing


def L(i, source="nb"):
    return Listing(source=source, source_id=str(i), deal="rent", title=f"flat {i}", url="u", feed=f"{source}:rent")


def test_users_and_login(database):
    uid = store.create_user("Me@Example.in", "password1")
    assert store.check_login("me@example.in", "password1") == uid
    assert store.check_login("me@example.in", "wrong") is None
    with pytest.raises(ValueError):
        store.create_user("me@example.in", "password1")
    with pytest.raises(ValueError):
        store.create_user("not-an-email", "password1")


def test_settings_validation_and_secret_masking(database):
    uid = store.create_user("a@b.in", "password1")
    s = store.save_settings(uid, {"deal": "both", "alerts": {"smtp_port": "587", "smtp_password": "pw", "sound": False},
                                  "schedule_min": 1, "bogus": 1})
    assert s["deal"] == "both" and s["alerts"]["smtp_port"] == 587 and s["alerts"]["sound"] is False
    assert s["schedule_min"] == 5 and "bogus" not in s           # clamped to the local minimum
    public = store.public_settings(s)
    assert public["alerts"]["smtp_password"] == store.MASK
    store.save_settings(uid, {"alerts": {"smtp_password": store.MASK}})
    assert store.load_settings(uid)["alerts"]["smtp_password"] == "pw"
    for bad in ({"deal": "x"}, {"area": {"lat": 200, "lon": 0, "radius_km": 1}}, {"custom_pages": [{"url": "ftp://x"}]}):
        with pytest.raises(ValueError):
            store.save_settings(uid, bad)


def test_record_reports_only_never_seen_listings_per_user(database):
    a = store.create_user("a@b.in", "password1")
    b = store.create_user("b@b.in", "password1")
    assert store.record(a, [L(1), L(2)], "t1") == {("nb", "1"), ("nb", "2")}
    assert store.record(a, [L(1), L(2), L(3)], "t2") == {("nb", "3")}
    assert store.record(b, [L(1)], "t2") == {("nb", "1")}           # other users have their own history
    store.mark_unread(a, {("nb", "3")})
    store.save_scan(a, "s1", "t0", "t2", {"x": 1}, [{**L(3).to_dict(), "tags": ["area"], "first_seen": "t2"}])
    r = store.last_results(a)
    assert r["listings"][0]["unread"] is True and store.last_results(b) is None
    assert store.mark_all_read(a) == 1


def test_baselines_and_buildings_are_per_user(database):
    a = store.create_user("a@b.in", "password1")
    b = store.create_user("b@b.in", "password1")
    store.add_baselines(a, "area:x", {"nb:rent"}, "t")
    store.add_baselines(a, "area:x", {"nb:rent", "mb:rent"}, "t")  # repeats are ignored
    assert store.baselined_feeds(a, "area:x") == {"nb:rent", "mb:rent"} and store.baselined_feeds(b, "area:x") == set()
    bld = store.add_building(a, {"name": "Mantri Classic", "lat": 12.93, "lon": 77.63, "deal": "both"})
    assert bld["radius_km"] == 1 and bld["deal"] == "both"
    assert store.list_buildings(b) == [] and not store.delete_building(b, bld["id"])
    assert store.delete_building(a, bld["id"]) and store.list_buildings(a) == []
    with pytest.raises(ValueError):
        store.add_building(a, {"name": "x", "lat": 1, "lon": 1})


def test_scheduled_users(database):
    a = store.create_user("a@b.in", "password1")
    store.create_user("b@b.in", "password1")
    store.save_settings(a, {"schedule_min": 30})
    assert store.scheduled_users() == [(a, 30)]
