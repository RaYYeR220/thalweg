# Thalweg infrastructure

AWS CDK (TypeScript) app for the Thalweg station network: clip ingest, the gauging workflow,
the API, the web front door, operations, and the Graviton4 worker.

```
station clip ─▶ S3 ingest ─(EventBridge)─▶ StartRun ─▶ Step Functions thalweg-gauging
                                                          Preflight (Lambda)
                                                          Agent      (activity thalweg-agent ◀── Graviton4 worker, COOL AMI)
                                                          Persist    (Lambda)  ─▶ DynamoDB runs / gaugings / stations, EMF metrics
                                                          warning draft? ─▶ AwaitSignature (.waitForTaskToken, 6 h)
                                                                              sign ─▶ IssueWarning ─▶ public/warnings.json
                                                                              reject ─▶ REJECTED · timeout ─▶ EXPIRED
                                                          any failure before Persist ─▶ FailClosed: run HOLD fail_closed:<error>
browser ─▶ CloudFront ─┬─ /            ─▶ S3 web (static export)
        (or thalweg-site)├─ /artifacts/* ─▶ S3 artifacts (runs/, stations/, public/ only)
                       └─ /api/*       ─▶ HTTP API ─▶ thalweg-api (Lambda, arm64)
```

Two front doors share one URL layout, so the web app runs unchanged behind either:

- **CloudFront** (`ThalwegWeb`): OAC to both buckets, viewer-request functions for the static
  export, the `/artifacts` allow-list and the `/api` prefix.
- **`thalweg-site`** (`ThalwegApi.SiteUrl`): a second HTTP API that serves `/` and
  `/artifacts/*` from S3 through the `thalweg-web` Lambda (objects over 4 MiB redirect to a
  presigned URL) and `/api/*` through the same router. It is the front door while CloudFront is
  unavailable to the account, and stays harmless once the distribution exists.

## Stacks

| Stack | Contents |
|---|---|
| `ThalwegData` | Buckets `thalweg-ingest-<acct>`, `thalweg-artifacts-<acct>`, `thalweg-web-<acct>`; tables `thalweg-stations`, `thalweg-gaugings`, `thalweg-runs` (GSI `by_station`), `thalweg-warnings` (GSI `by_state`), `thalweg-budget` |
| `ThalwegFlow` | Activity `thalweg-agent`, state machine `thalweg-gauging` ([definition](../statemachine/gauging.asl.json)), task Lambdas, rule `thalweg-clip-arrived`, schedule `thalweg-replay` (created disabled) |
| `ThalwegApi` | HTTP API `thalweg` + router Lambda `thalweg-api`; HTTP API `thalweg-site` + Lambda `thalweg-web` |
| `ThalwegWeb` | CloudFront distribution (OAC to the web and artifacts buckets), three viewer-request functions |
| `ThalwegOps` | Dashboard `thalweg`, alarms `thalweg-executions-failed` and `thalweg-worker-silent` (SNS `thalweg-alarms`), AWS Budget `thalweg` |
| `ThalwegWorker` | Only with `-c worker=true -c coolAmi=<ami>`: the c8g.large worker, its role and `/etc/thalweg.env` |

Everything is tagged `project=thalweg`. Lambdas are Python 3.12 on arm64 (handlers in
[`../services`](../services)), logs keep 14 days, tables are on-demand, buckets use SSE-S3.
Stations and gaugings tables and the artifacts bucket are retained if the stacks are deleted.

## Deploy

Prerequisites: Node 20+, AWS credentials for the target account, the account bootstrapped for
CDK (`npx cdk bootstrap`).

```bash
cd infra
npm ci

# 1. Parameters CloudFormation does not hold (once per account; existing ones are kept).
VENICE_API_KEY=... THALWEG_NOTIFY_EMAIL=ops@example.org \
  scripts/put-params.sh --profile <profile>

# 2. Synthesize and deploy (the worker stack is not part of --all unless asked for).
npx cdk synth --profile <profile>
npx cdk deploy --all --require-approval never --profile <profile>

# 3. End-to-end smoke test through the front door (cleans up after itself).
python scripts/smoke.py --profile <profile>
```

New AWS accounts can be refused CloudFront distributions until AWS Support verifies them
("Your account must be verified before you can add new CloudFront resources"). Until then,
deploy the other stacks and use `ThalwegApi.SiteUrl`; once verified, `npx cdk deploy ThalwegWeb`
adds the distribution in front of the same buckets and API:

```bash
npx cdk deploy ThalwegData ThalwegFlow ThalwegApi ThalwegOps --require-approval never --profile <profile>
```

Publishing the web app: `aws s3 sync web/out s3://thalweg-web-<acct>/ --delete`, with
`--cache-control no-cache` for `*.html` (the site API applies the same defaults when an object
has no Cache-Control: HTML `no-cache`, `_next/static/*` immutable, everything else 5 minutes).

The budget filters on the cost-allocation tag `user:project`. Billing only learns a tag key once
tagged resources have incurred cost (up to 24 h after the first deploy); then activate it once:

```bash
aws ce update-cost-allocation-tags-status --cost-allocation-tags-status TagKey=project,Status=Active --profile <p>
```

`THALWEG_NOTIFY_EMAIL` ends up in the String parameter `/thalweg/notify_email`, which the Ops
stack resolves at deploy time for the budget alert and the alarm topic (confirm the SNS
subscription mail once). Handler tests: `python -m pytest services/tests -q` from the repo root.
CDK assertions: `npm test` here.

### Parameters

| Name | Type | Use |
|---|---|---|
| `/thalweg/venice_api_key` | SecureString | LLM brain on the worker |
| `/thalweg/reviewer_passcode` | SecureString | `POST /reviews/{id}`; read it with `aws ssm get-parameter --with-decryption --name /thalweg/reviewer_passcode --query Parameter.Value --output text` |
| `/thalweg/mcp_token` | SecureString | bearer token of the worker's MCP server; also marks `POST /runs` callers as `requested_by: "api"` |
| `/thalweg/notify_email` | String | budget and alarm notifications |

### Outputs

`ThalwegWeb.SiteUrl` (the product behind CloudFront), `ThalwegApi.SiteUrl` (the same layout
without the CDN), `ThalwegApi.ApiUrl` (bare routes, CORS for `http://localhost:3000`), `ThalwegFlow.ActivityArn`, `ThalwegFlow.StateMachineArn`,
`ThalwegOps.DashboardUrl`, plus every bucket and table name on `ThalwegData`.

## The replay schedule

`thalweg-replay` feeds the River Dart station one archived Storm Callum clip every 15 minutes
so it keeps gauging during judging. It reads
`s3://thalweg-artifacts-<acct>/data/dart/replay-manifest.json`:

```json
{"station_id": "dart", "clips": [{"video_key": "data/dart/...mp4", "t": "2018-10-12T06:00:00Z", "level": 1.23}]}
```

copies the clip at `replay_cursor` (on the `dart` item in `thalweg-stations`) to
`clips/dart/<YYYYMMDDTHHMMSS>.mp4`, writes the sidecar with `brain: "llm"`,
`model: "claude-haiku-5-5"`, `requested_by: "schedule"`, and wraps around at the end. The
station item must exist first. Turn it on once the manifest and clips are uploaded:

```bash
npx cdk deploy ThalwegFlow -c replay=enabled --profile <p>
```

Keep passing `-c replay=enabled` on later deploys of `ThalwegFlow` (or `--all`); a deploy
without it puts the schedule back to disabled. Run one replay right away with
`aws lambda invoke --function-name thalweg-replay out.json`.

## The worker stack

The worker runs on the COOL Graviton4 AMI (AWS Marketplace, subscribe first). It is a
`c8g.large` in a public subnet of the default VPC with no inbound rules (SSM Session Manager
only), IMDSv2 required and a 40 GB gp3 root volume.

```bash
npx cdk deploy ThalwegWorker -c worker=true -c coolAmi=ami-0123456789abcdef0 --profile <p>
# options: -c workerInstanceType=c8g.xlarge  -c rootDevice=/dev/sda1  -c marketplaceMetering=true
aws ssm start-session --target <InstanceId> --profile <p>
```

The instance role may poll and answer the `thalweg-agent` activity, read the ingest bucket,
read and write the artifacts bucket and the `thalweg-*` tables, read `/thalweg/*` parameters,
put metrics in the `Thalweg` namespace and write the `/thalweg/worker` log group. User data
writes the resource names (never secrets) to `/etc/thalweg.env`.

### Worker contract

The activity input is the Preflight output:
`{run_id, station_id, clip_key, sidecar_key, brain, model?, requested_by, t, level, source, started, clip_bytes, ingest_bucket, artifacts_bucket, station_config_key?}`.
Send a heartbeat at least every 120 s; the task times out 900 s after it is scheduled
(queue time included). Reply with `SendTaskSuccess`:

```json
{
  "run_id": "r-20261026091500-1a2b3c",
  "decision": {"kind": "PUBLISH", "reasons": ["..."], "q": 61.2, "q_lo": 55.0, "q_hi": 67.0, "method": "lspiv+stiv"},
  "gauging": {"station_id": "dart", "t": "...", "level": 1.42, "q": 61.2, "q_lo": 55.0, "q_hi": 67.0, "method": "lspiv+stiv", "run_id": "..."},
  "warning_draft": {"id": "w-dart-...", "station_id": "dart", "created": "...", "level_now": 2.3, "q_now": 180,
                    "eta_s": 5400, "p_cross": 0.8, "text": "...", "evidence": ["runs/<run_id>/..."], "expires": "..."},
  "trace_key": "runs/<run_id>/trace.json",
  "cost_usd": 0.003,
  "timings": {"total_s": 41.5},
  "policy_denials": 1
}
```

`gauging` is required with PUBLISH and refused otherwise; `warning_draft` is only carried
forward after a PUBLISH. A result that breaks the contract fails the run closed (HOLD), as does
`SendTaskFailure` or a missed heartbeat. While running, upload `runs/<run_id>/trace.json` with
`Cache-Control: max-age=5` so the run page can follow it through CloudFront. The daily LLM
spend counter belongs in `thalweg-budget` (item per UTC day, `day = YYYY-MM-DD`); the API keeps
its judge-run counter on the same item (`judge_llm_runs`, 40 per day).

## API

All JSON, served at `<front door>/api` (bare on `ThalwegApi.ApiUrl`).

| Route | Notes |
|---|---|
| `GET /health` | `{ok, service, time, version}` |
| `GET /stations` | `{stations: [...]}` with latest fields `last_t, last_level, last_q, last_decision, ...` |
| `GET /stations/{id}` | station item, 404 if unknown |
| `GET /stations/{id}/gaugings?since=&limit=` | `{station_id, count, gaugings}` oldest first; without `since`, the latest `limit` (default 500, max 5000) |
| `GET /runs?station=&limit=` | `{runs: [...]}` newest first (default 50) |
| `GET /runs/{id}` | run item + `trace_url` (`/artifacts/runs/<id>/trace.json`) + `trace_presigned_url` (1 h) |
| `POST /runs` | `{station_id, clip_key, brain: "autopilot"\|"llm", model?}` → 202 `{run_id, status: "RUNNING", deduplicated: false}`; while a run for the same `(clip_key, brain)` is RUNNING → 200 with that `run_id` and `deduplicated: true`; judge LLM runs over the daily cap → 429 |
| `GET /reviews?state=DRAFT\|SIGNED\|REJECTED\|EXPIRED` | `{state, reviews}` newest first (task tokens never leave the table) |
| `POST /reviews/{id}` | `{action: "sign"\|"reject", passcode, note}` → 200 `{next_state}`; wrong passcode 403; not a DRAFT (or already being acted on) 409 |

## Metrics

Namespace `Thalweg` (emitted by Persist and FailClosed as EMF): `Decision` by `kind`
(PUBLISH/HOLD/ESCALATE), `GaugingLatency` (s, run start to persisted decision), `LLMCostUSD`,
`PolicyDenials`. Six custom metrics in total.
