# Thalweg

**A camera is a stream gauge.** Thalweg reads river video with OpenCV 5 and turns it into a discharge measurement (m³/s). An agent decides whether the measurement can be trusted. When the evidence doesn't hold, it refuses to publish: it asks for a better clip or hands the case to a hydrologist. The station also keeps a rating curve and a stage nowcast. When a flood threshold is coming, it drafts a warning that only a person can sign.

- **Live station:** River Dart at Austins Bridge (UK), replaying Storm Callum (12 Oct 2018) → <!--SITE-->
- **Benchmark:** blind, against 15–23 human hydrologists on the 8 real videos of the Video Globe Challenge 2020 → [`eval/`](eval/)
- **Runs on:** AWS Graviton4 with COOL (Cloud-Optimized OpenCV), Step Functions, DynamoDB, S3, API Gateway

<!--HERO-->

## Why

Floods affect more people than any other weather hazard, and the gauging network that tells us how much water is coming is thin. Gauging a river in flood with a current meter or an ADCP boat is slow, expensive and dangerous. Video gauging solves the cost and the danger: a camera, a few surveyed reference points and a cross-section are enough.

What it doesn't solve is the operator. Every frame step, window size, filter and coefficient is a manual choice. In the Video Globe Challenge 2020, 15–23 hydrologists measured the same eight videos. On a typical video their median error was 10–23%, and a few answers had the wrong sign. Bodart et al. (2023) showed that these parameter choices are the dominant error source. A camera network can't scale if every clip needs an expert.

Thalweg is that operator. It looks at each clip and chooses the method and parameters from what it sees. It cross-checks two independent measurements and publishes only on a basis the code can verify.

## How it measures (OpenCV 5)

| Step | What happens | OpenCV 5 |
|---|---|---|
| Camera check | The clip is compared with the station's surveyed reference view. A moved camera is escalated, never gauged. | `ALIKED_create` + `LightGlueMatcher_create`, robust fit |
| Stabilize | Hand-held, drone and vibrating cameras are registered to the reference frame on the banks only. | ALIKED + LightGlue, RANSAC similarity; phase correlation as a check |
| Orthorectify | Surveyed ground reference points give the camera model: a homography for planar points, DLT + `solvePnP` for 3D ones. Every frame is remapped onto the **water surface at the live water level**. That is a different plane at every stage. | `findHomography(USAC_MAGSAC)`, `solvePnP`, `projectPoints`, `remap` |
| Diagnose | Moving-tracer energy, static texture ("riverbed visible through clear water"), glare, darkness, blur and frozen frames, measured on the orthorectified water. | Laplacian, temporal high-pass, connected components |
| Velocimetry | Three independent methods: **LSPIV** (ensemble cross-correlation, sub-pixel Gaussian peak), **STIV** (space-time images; shift-and-add streak alignment), **dense DIS optical flow**. Each comes with a normalized median test. | `matchTemplate(TM_CCOEFF_NORMED)`, `remap`, `DISOpticalFlow` |
| Discharge | Velocity-area (midsection) over the surveyed cross-section: surface velocity × α × depth × width per panel, a depth²ᐟ³ fill for gaps, and an uncertainty budget. | numpy/scipy |

## The agent, and why it refuses

The mandatory perception steps always run first. A brain then chooses the next tool calls. It is either the deterministic **autopilot** or an **LLM** with tool calling and image input (Claude Sonnet 5.5, served by Venice). Every call passes a **policy gate written in code**:

- Parameters are bounded. Prerequisites must exist. There are step and time budgets.
- `publish_gauging` must name its basis. The gate checks that basis against the measured evidence:
  - `methods_agree`: two methods within 15%;
  - `bed_visible_prefers_stiv`: the static-texture ratio really is above 1.2;
  - `seeded_surface_prefers_lspiv`: the surface really is well seeded.

  Geometry, stabilization, coverage and image quality must also pass. A false basis is denied, the denial is recorded in the trace, and the brain must fix the problem or escalate.
- LLM timeout, malformed output, a tool exception, or an exhausted daily budget all lead to **HOLD**. The run fails closed and never produces a guessed number.
- There is no tool that issues a flood warning. After a published gauging, the station refits its rating curve (Q = a(h − h₀)ᵇ) and runs a constant-velocity Kalman nowcast on the stage. If the probability of crossing the warning threshold within 3 h is at least 0.5, it **drafts** a warning. A Step Functions `waitForTaskToken` holds it until a hydrologist signs or rejects it in the review queue (6 h expiry).

Every run leaves a trace: each tool call, its arguments and stated reason, the policy verdict, the evidence images and the decision. The trace shows the OpenCV output changing the next action.

## Results

### Blind benchmark: Video Globe Challenge 2020

Videos 1 and 8 were used for development. The agent, policy and parameters were then frozen (`git tag eval-freeze`). Videos 2–7 (two ground cameras, one 3D-surveyed bank camera, one more ground camera and three drone flights over rivers in France, Norway and Australia) were scored once per brain. The scorer reads the reference discharges; the pipeline never does. Every run is appended to [`eval/runs.jsonl`](eval/runs.jsonl).

<!--BENCH-->

Full per-video results, including the experts' errors and our unpublished estimates: [`eval/results/benchmark.json`](eval/results/benchmark.json).

### Refusal suite

Real clips corrupted in known ways, each with the decision the agent must reach: night, glare, frozen feed, still water, camera moved, level above the banks, hand-held shake, and clean controls. The number that matters is **wrong publications: 0**, in every run. The first frozen run also exposed real robustness bugs. A dark frame was reported as a moved camera, and stabilization was rejected on hand-held footage. They were fixed in v1.1, and both versions are published.

<!--REFUSAL-->

### Performance: COOL on Graviton4

<!--COOL-->

## AWS

![architecture](docs/architecture.svg)

- **Ingest:** a station uploads a clip and a sidecar (`t`, `level`) to S3. EventBridge then starts the `thalweg-gauging` state machine.
- **Worker:** one EC2 `c8g.large` (Graviton4) from the COOL Marketplace AMI. It polls a Step Functions activity, runs the agent with COOL's OpenCV 5.1 build (KleidiCV + SVE), and writes evidence images and the trace to S3. It is reached only through SSM; there are no inbound ports.
- **State:** DynamoDB (stations, gaugings, runs, warnings, the LLM budget). Secrets are SecureStrings in SSM Parameter Store.
- **Human signature:** `waitForTaskToken`. The review API calls `SendTaskSuccess` / `SendTaskFailure`, and a draft expires after 6 h.
- **Operations:** CloudWatch dashboard, EMF metrics (decisions by kind, latency, policy denials, LLM cost), alarms on failed executions and a silent worker, and an AWS Budget on the `project=thalweg` tag. Everything is defined in AWS CDK ([`infra/`](infra/)).

## Run it

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
python scripts/fetch_models.py                 # ALIKED + LightGlue ONNX (52 MB, SHA-1 checked)
python scripts/fetch_data.py vgc2020           # Video Globe Challenge 2020 (1.1 GB, etalab-2.0)
export THALWEG_DATA=~/.cache/thalweg/data
thalweg gauge --station stations/vgc1.yaml --clip clips/vgc1.json --brain autopilot
pytest -q
```

The LLM brain needs `VENICE_API_KEY` (`--brain llm`). The MCP server (`python mcp_server/server.py`) exposes the gauging tools to any MCP client.

## Honest limits

- Stage (water level) comes from the station's level sensor or the dataset; Thalweg does not read it from the image.
- Cross-sections and reference points are surveyed inputs. A station whose survey is wrong will be gauged wrong. The camera-pose check only catches the camera moving.
- The surface coefficient α is the station's value (0.85 by default). It is not measured.
- The benchmark has six held-out videos. That is enough to compare with the experts on the same footage, not enough for a general accuracy claim.
- Storm Callum is a replay of archived footage, not a live feed. Times on the live station are 2018 clip times.
- The warning threshold is the NRFA QMED for the Dart at Austins Bridge (231.2 m³/s), a hydrologically meaningful flood index. It is not the Environment Agency's operational trigger.

## Data and credits

- Video Globe Challenge 2020: Bodart, Le Coz, Jodeau, Hauet (2023), doi:10.57745/KVNIHX, etalab-2.0. Videos supplied by EDF, DREAL Auvergne-Rhône-Alpes, NVE and DNRME Queensland.
- River Dart image velocimetry: Perks (2024), doi:10.25405/data.ncl.19762027, CC BY 4.0.
- Flow statistics: UK National River Flow Archive, station 46003.
- ALIKED / LightGlue ONNX models via the OpenCV test-data collection.

MIT licensed.
