"""Moto-backed fixtures mirroring the tables and buckets the CDK app creates."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import boto3
import pytest
from botocore.exceptions import ClientError

SERVICES = Path(__file__).resolve().parents[1]
if str(SERVICES) not in sys.path:
    sys.path.insert(0, str(SERVICES))

ACCOUNT = "123456789012"
REGION = "us-east-1"
INGEST = "thalweg-ingest-test"
ARTIFACTS = "thalweg-artifacts-test"
WEB = "thalweg-web-test"
SM_ARN = f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:thalweg-gauging"
PASSCODE = "k3y5t0ne"
SERVICE_TOKEN = "0123456789abcdef0123456789abcdef"

os.environ.pop("AWS_PROFILE", None)
os.environ.update(
    {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_REGION": REGION,
        "INGEST_BUCKET": INGEST,
        "ARTIFACTS_BUCKET": ARTIFACTS,
        "WEB_BUCKET": WEB,
        "STATE_MACHINE_ARN": SM_ARN,
        "TABLE_STATIONS": "thalweg-stations",
        "TABLE_GAUGINGS": "thalweg-gaugings",
        "TABLE_RUNS": "thalweg-runs",
        "TABLE_WARNINGS": "thalweg-warnings",
        "TABLE_BUDGET": "thalweg-budget",
        "PASSCODE_PARAM": "/thalweg/reviewer_passcode",
        "SERVICE_TOKEN_PARAM": "/thalweg/mcp_token",
        "JUDGE_LLM_DAILY_CAP": "40",
        "JUDGE_MODEL": "claude-sonnet-5-5",
        "ALLOWED_MODELS": "claude-sonnet-5-5,claude-haiku-5-5",
        "REPLAY_MODEL": "claude-haiku-5-5",
    }
)

from moto import mock_aws  # noqa: E402

from common import aws, params  # noqa: E402


def _client_error(code: str, op: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, op)


@dataclass
class FakeSfn:
    """Records Step Functions calls; set `fail_with` to make the next call raise that error code."""

    calls: list[tuple[str, dict]] = field(default_factory=list)
    fail_with: str | None = None

    def _call(self, op: str, kw: dict) -> None:
        self.calls.append((op, kw))
        if self.fail_with:
            code, self.fail_with = self.fail_with, None
            raise _client_error(code, op)

    def start_execution(self, **kw):
        self._call("start_execution", kw)
        return {"executionArn": SM_ARN.replace(":stateMachine:", ":execution:") + ":" + kw["name"], "startDate": 0}

    def send_task_success(self, **kw):
        self._call("send_task_success", kw)
        return {}

    def send_task_failure(self, **kw):
        self._call("send_task_failure", kw)
        return {}

    def named(self, op: str) -> list[dict]:
        return [kw for name, kw in self.calls if name == op]


@dataclass
class Env:
    ddb: object
    s3: object
    sfn: FakeSfn

    def table(self, name: str):
        return self.ddb.Table(name)

    def put_json(self, bucket: str, key: str, doc) -> None:
        self.s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc).encode())

    def get_json(self, bucket: str, key: str):
        return json.loads(self.s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    def add_station(self, sid: str = "dart", **extra) -> None:
        self.table("thalweg-stations").put_item(Item={"id": sid, "name": sid.title(), **extra})

    def add_clip(self, station: str = "dart", stamp: str = "20181012T120000", level: float = 1.42, **sidecar) -> str:
        clip_key = f"clips/{station}/{stamp}.mp4"
        self.s3.put_object(Bucket=INGEST, Key=clip_key, Body=b"\x00\x00\x00\x18ftypmp42")
        doc = {"station_id": station, "t": "2018-10-12T12:00:00Z", "level": level, "source": "test", **sidecar}
        self.put_json(INGEST, f"clips/{station}/{stamp}.json", doc)
        return clip_key


def _create_tables(ddb) -> None:
    def table(name, keys, gsis=None):
        attrs = {}
        schema = []
        for i, (k, t) in enumerate(keys):
            attrs[k] = t
            schema.append({"AttributeName": k, "KeyType": "HASH" if i == 0 else "RANGE"})
        kwargs = {"TableName": name, "KeySchema": schema, "BillingMode": "PAY_PER_REQUEST"}
        if gsis:
            kwargs["GlobalSecondaryIndexes"] = []
            for gname, gkeys in gsis.items():
                gschema = []
                for i, (k, t) in enumerate(gkeys):
                    attrs[k] = t
                    gschema.append({"AttributeName": k, "KeyType": "HASH" if i == 0 else "RANGE"})
                kwargs["GlobalSecondaryIndexes"].append(
                    {"IndexName": gname, "KeySchema": gschema, "Projection": {"ProjectionType": "ALL"}}
                )
        kwargs["AttributeDefinitions"] = [{"AttributeName": k, "AttributeType": t} for k, t in attrs.items()]
        ddb.create_table(**kwargs)

    table("thalweg-stations", [("id", "S")])
    table("thalweg-gaugings", [("station_id", "S"), ("t", "S")])
    table("thalweg-runs", [("run_id", "S")], {"by_station": [("station_id", "S"), ("started", "S")]})
    table("thalweg-warnings", [("id", "S")], {"by_state": [("state", "S"), ("created", "S")]})
    table("thalweg-budget", [("day", "S")])


@pytest.fixture
def env():
    with mock_aws():
        aws.reset()
        params.reset()
        ddb = boto3.resource("dynamodb", region_name=REGION)
        s3 = boto3.client("s3", region_name=REGION)
        ssm = boto3.client("ssm", region_name=REGION)
        _create_tables(ddb)
        for b in (INGEST, ARTIFACTS, WEB):
            s3.create_bucket(Bucket=b)
        ssm.put_parameter(Name="/thalweg/reviewer_passcode", Value=PASSCODE, Type="SecureString")
        ssm.put_parameter(Name="/thalweg/mcp_token", Value=SERVICE_TOKEN, Type="SecureString")
        fake = FakeSfn()
        aws.override("stepfunctions", fake)
        yield Env(ddb=ddb, s3=s3, sfn=fake)
        aws.reset()
        params.reset()

