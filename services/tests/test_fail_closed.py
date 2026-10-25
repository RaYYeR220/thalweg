from decimal import Decimal

from workflow import fail_closed

RUN_ID = "r-20261026090000-abc123"


def test_activity_timeout_holds_the_run(env):
    env.add_station("dart")
    env.table("thalweg-runs").put_item(Item={"run_id": RUN_ID, "station_id": "dart", "status": "RUNNING", "started": "2026-10-26T09:00:00Z"})

    out = fail_closed.handler({"run_id": RUN_ID, "station_id": "dart", "error": {"Error": "States.Timeout", "Cause": None}})

    assert out["reason"] == "fail_closed:timeout"
    run = env.table("thalweg-runs").get_item(Key={"run_id": RUN_ID})["Item"]
    assert run["status"] == "HOLD"
    assert run["decision"] == {"kind": "HOLD", "reasons": ["fail_closed:timeout"]}
    assert run["started"] == "2026-10-26T09:00:00Z"
    assert env.table("thalweg-stations").get_item(Key={"id": "dart"})["Item"]["last_decision"] == "HOLD"


def test_failure_before_preflight_still_records_the_run(env):
    out = fail_closed.handler(
        {
            "run_id": RUN_ID,
            "station_id": "ghost",
            "clip_key": "clips/ghost/a.mp4",
            "brain": "autopilot",
            "error": {"Error": "StationNotFound", "Cause": '{"errorMessage": "ghost", "errorType": "StationNotFound"}'},
        }
    )
    assert out["reason"] == "fail_closed:StationNotFound" and out["cause"] == "ghost"
    run = env.table("thalweg-runs").get_item(Key={"run_id": RUN_ID})["Item"]
    assert run["status"] == "HOLD" and run["clip_key"] == "clips/ghost/a.mp4" and "started" in run
    # No station item is invented for an unknown station.
    assert "Item" not in env.table("thalweg-stations").get_item(Key={"id": "ghost"})


def test_a_half_persisted_gauging_is_withdrawn(env):
    env.add_station("dart")
    env.table("thalweg-gaugings").put_item(Item={"station_id": "dart", "t": "2018-10-12T12:00:00Z", "q": Decimal("61.2"), "run_id": RUN_ID})
    env.table("thalweg-gaugings").put_item(Item={"station_id": "dart", "t": "2018-10-12T11:45:00Z", "q": Decimal("58.0"), "run_id": "r-20261026084500-000000"})

    fail_closed.handler(
        {
            "run_id": RUN_ID,
            "station_id": "dart",
            "t": "2018-10-12T12:00:00Z",
            "agent": {"gauging": {"t": "2018-10-12T12:00:00Z", "q": 61.2}},
            "error": {"Error": "Lambda.ServiceException"},
        }
    )
    keys = {it["t"] for it in env.table("thalweg-gaugings").scan()["Items"]}
    assert keys == {"2018-10-12T11:45:00Z"}
