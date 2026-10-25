# Claims and their evidence

Every public statement about Thalweg, tagged by how it can be checked.

- **REPRODUCIBLE**: rerun it from this repository and get the same result.
- **VERIFIED-LIVE**: check it on the running deployment.
- **MODELED**: an estimate built on stated assumptions.
- **NOT-CLAIMED**: something we explicitly do not claim.

| Claim | Tier | Evidence |
|---|---|---|
| OpenCV 5 does all perception: ALIKED + LightGlue, USAC, `solvePnP`, `remap`, DIS, `matchTemplate` | REPRODUCIBLE | `thalweg/vision/`, `tests/test_smoke.py` |
| LSPIV and STIV recover a known synthetic surface velocity within 3–5% | REPRODUCIBLE | `tests/vision/test_velocimetry.py` |
| The agent cannot publish on a basis the measurements contradict | REPRODUCIBLE | `thalweg/agent/policy.py`, `tests/agent/test_agent.py::test_false_basis_is_denied_then_escalated` |
| LLM timeout, budget exhaustion or a looping brain all end in HOLD | REPRODUCIBLE | `tests/agent/test_agent.py` |
| No agent tool can issue a flood warning; only a person can sign one | REPRODUCIBLE + VERIFIED-LIVE | `thalweg/agent/tools.py` (no such tool), `services/api/reviews.py`, `statemachine/gauging.asl.json`; the review page on the live site |
| Blind VGC2020 results (frozen autopilot and LLM) | REPRODUCIBLE | `git tag eval-freeze`, `eval/results/vgc-*/`, `eval/runs.jsonl` |
| v1.1 results | REPRODUCIBLE, **not blind** | `eval/results/*-v1.1/` |
| Refusal suite: no wrong publications | REPRODUCIBLE | `eval/results/refusal-*/summary.json` |
| The worker runs COOL's OpenCV on Graviton4 | VERIFIED-LIVE + REPRODUCIBLE | `bench/results/c8g-large-cool.json` (`cv2.__file__`, build HAL lines, IMDS instance type and AMI); worker logs |
| COOL vs stock OpenCV vs x86 timings and $/1000 gaugings | REPRODUCIBLE | `bench/run_bench.py`, `bench/results/` (5 repeats, median) |
| Cost per 1000 gaugings | MODELED | On-demand instance price plus COOL software fee, times measured seconds per clip. It excludes storage, requests and the LLM. |
| The Storm Callum warning would have been drafted about 1.5 h before the stage passed the QMED level | MODELED | The nowcast rule replayed on the published stage series. The rating curve maps QMED to a stage, so this depends on that curve. |
| The live station gauges Storm Callum every 10 minutes | VERIFIED-LIVE | The station page and the runs list. It is a replay of 2018 footage. |

## Not claimed

- That Thalweg is more accurate than expert hydrologists in general. On six held-out videos it ranks from the top to the bottom of the expert field; see the per-video results.
- Any measurement of water level from the image.
- Validity of the surface coefficient α under wind; video 5 shows it fails.
- Real-time video streaming. Stations upload short clips.
- That the warning threshold is an official Environment Agency trigger. It is the NRFA QMED flood index.
- Load testing beyond one worker.

## Mocks and simulated parts

There are none in the measurement path. The stage values come from the datasets. The Storm Callum station is a time-shifted replay of real archived clips through the real pipeline. Synthetic videos are used only in unit tests.
