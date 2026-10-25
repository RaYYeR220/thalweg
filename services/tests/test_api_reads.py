from decimal import Decimal

from _http import call


def test_health(env):
    status, body = call("GET /health")
    assert status == 200 and body["ok"] is True and body["time"].endswith("Z")


def test_stations_empty_and_listed(env):
    assert call("GET /stations") == (200, {"stations": []})
    env.add_station("vgc1")
    env.add_station("dart", last_q=Decimal("61.2"), last_decision="PUBLISH")
    status, body = call("GET /stations")
    assert status == 200
    assert [s["id"] for s in body["stations"]] == ["dart", "vgc1"]
    assert body["stations"][0]["last_q"] == 61.2


def test_station_by_id(env):
    env.add_station("dart")
    assert call("GET /stations/{id}", path={"id": "dart"}) == (200, {"id": "dart", "name": "Dart"})
    status, body = call("GET /stations/{id}", path={"id": "nope"})
    assert status == 404 and body["error"] == "station_not_found"


def test_gaugings_since_and_limit(env):
    t = env.table("thalweg-gaugings")
    for i in range(6):
        t.put_item(Item={"station_id": "dart", "t": f"2018-10-12T0{i}:00:00Z", "q": Decimal(str(10 + i)), "run_id": f"r-{i}"})

    status, body = call("GET /stations/{id}/gaugings", path={"id": "dart"})
    assert status == 200 and body["count"] == 6 and body["gaugings"][0]["t"] == "2018-10-12T00:00:00Z"

    _, body = call("GET /stations/{id}/gaugings", path={"id": "dart"}, query={"since": "2018-10-12T03:00:00+00:00", "limit": "2"})
    assert [g["t"] for g in body["gaugings"]] == ["2018-10-12T03:00:00Z", "2018-10-12T04:00:00Z"]

    # Without `since`: the latest N, oldest first.
    _, body = call("GET /stations/{id}/gaugings", path={"id": "dart"}, query={"limit": "2"})
    assert [g["q"] for g in body["gaugings"]] == [14, 15]

    assert call("GET /stations/{id}/gaugings", path={"id": "dart"}, query={"since": "yesterday"})[0] == 400
    assert call("GET /stations/{id}/gaugings", path={"id": "dart"}, query={"limit": "0"})[0] == 400


def _run(env, rid, station, started, **extra):
    env.table("thalweg-runs").put_item(Item={"run_id": rid, "station_id": station, "started": started, "status": "PUBLISH", **extra})


def test_runs_list_and_detail(env):
    _run(env, "r-20261026090000-aaaaaa", "dart", "2026-10-26T09:00:00Z", trace_key="runs/r-20261026090000-aaaaaa/trace.json", execution_arn="arn:x")
    _run(env, "r-20261026091500-bbbbbb", "dart", "2026-10-26T09:15:00Z")
    _run(env, "r-20261026092000-cccccc", "vgc1", "2026-10-26T09:20:00Z")
    env.table("thalweg-runs").put_item(Item={"run_id": "lock#abc", "holder": "r-20261026092000-cccccc"})

    _, body = call("GET /runs", query={"station": "dart"})
    assert [r["run_id"] for r in body["runs"]] == ["r-20261026091500-bbbbbb", "r-20261026090000-aaaaaa"]
    _, body = call("GET /runs", query={"limit": "2"})
    assert [r["run_id"] for r in body["runs"]] == ["r-20261026092000-cccccc", "r-20261026091500-bbbbbb"]

    status, run = call("GET /runs/{id}", path={"id": "r-20261026090000-aaaaaa"})
    assert status == 200 and run["status"] == "PUBLISH"
    assert run["trace_url"] == "/artifacts/runs/r-20261026090000-aaaaaa/trace.json"
    assert "X-Amz-Signature=" in run["trace_presigned_url"]
    assert "execution_arn" not in run

    # A run without a trace yet points at where the worker streams it.
    _, run = call("GET /runs/{id}", path={"id": "r-20261026091500-bbbbbb"})
    assert run["trace_url"] == "/artifacts/runs/r-20261026091500-bbbbbb/trace.json"

    assert call("GET /runs/{id}", path={"id": "lock#abc"})[0] in (400, 404)
    assert call("GET /runs/{id}", path={"id": "r-20990101000000-000000"})[0] == 404


def test_unknown_route(env):
    status, body = call("DELETE /stations")
    assert status == 404 and body["error"] == "not_found"
