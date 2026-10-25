from conftest import ARTIFACTS, INGEST

from workflow import replay

MANIFEST = {
    "station_id": "dart",
    "clips": [
        {"video_key": "data/dart/clips/a.mp4", "t": "2018-10-12T06:00:00Z", "level": 1.10},
        {"video_key": "data/dart/clips/b.mp4", "t": "2018-10-12T06:15:00Z", "level": 1.25},
    ],
}


def _seed(env):
    env.add_station("dart")
    env.put_json(ARTIFACTS, "data/dart/replay-manifest.json", MANIFEST)
    for c in MANIFEST["clips"]:
        env.s3.put_object(Bucket=ARTIFACTS, Key=c["video_key"], Body=b"mp4:" + c["video_key"].encode())


def test_replay_feeds_the_next_clip_and_wraps(env):
    _seed(env)

    first = replay.handler({})
    assert first["index"] == 0 and first["clip_key"] == "clips/dart/20181012T060000.mp4"
    body = env.s3.get_object(Bucket=INGEST, Key="clips/dart/20181012T060000.mp4")["Body"].read()
    assert body == b"mp4:data/dart/clips/a.mp4"
    sidecar = env.get_json(INGEST, "clips/dart/20181012T060000.json")
    assert sidecar["station_id"] == "dart" and sidecar["t"] == "2018-10-12T06:00:00Z"
    assert sidecar["level"] == 1.10
    assert sidecar["brain"] == "llm" and sidecar["model"] == "claude-haiku-5-5" and sidecar["requested_by"] == "schedule"

    assert replay.handler({})["index"] == 1
    assert replay.handler({})["index"] == 0  # wrapped
    assert int(env.table("thalweg-stations").get_item(Key={"id": "dart"})["Item"]["replay_cursor"]) == 1


def test_replay_waits_for_the_station(env):
    env.put_json(ARTIFACTS, "data/dart/replay-manifest.json", MANIFEST)
    assert replay.handler({}) == {"skipped": "station_missing", "station_id": "dart"}
