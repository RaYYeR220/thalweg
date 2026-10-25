# Reviewing Thalweg in five minutes

1. **Open the live station.** https://jp89re6ozf.execute-api.us-east-1.amazonaws.com/station/
   This is the River Dart at Austins Bridge, replaying Storm Callum (12 Oct 2018). Look at:
   - the hydrograph (published gaugings over the stage series), the rating curve and the forecast band;
   - the latest evidence frame, with velocity vectors over the orthorectified river.

2. **Open any run** from the station's run list.
   The trace shows each step: the OpenCV 5 tool, its arguments, the agent's stated reason, the policy verdict (denials appear in red), the evidence image, and the final decision. Look for the step where the diagnostics change the plan. For example, `static_ratio` above 1.2 makes the agent prefer STIV and keep LSPIV only as a cross-check.

3. **Run the agent yourself.** On the station page, press **Run agent on this clip** (no account needed).
   - It starts a real Step Functions execution on the Graviton4 worker (COOL OpenCV), using the LLM brain.
   - The run page fills in as the steps complete, in about a minute.
   - Runs are capped at 40 a day.

4. **Sign or reject a flood-warning draft.** Open https://jp89re6ozf.execute-api.us-east-1.amazonaws.com/review/ with the reviewer passcode given in the submission form.
   - Signing resumes the paused Step Functions execution (`waitForTaskToken`). Signing twice returns 409.
   - The agent has no tool that can do this.

5. **Check the numbers.**
   - Blind benchmark against 15–23 hydrologists: https://jp89re6ozf.execute-api.us-east-1.amazonaws.com/benchmark/ and [`eval/results/benchmark.json`](eval/results/benchmark.json). The protocol is in [`eval/README.md`](eval/README.md). The freeze is `git tag eval-freeze`, and every run is in [`eval/runs.jsonl`](eval/runs.jsonl).
   - Refusal suite: the same files.
   - COOL vs stock OpenCV vs x86: [`bench/results/`](bench/results/).

6. **Reproduce locally.** Follow the "Run it" section of the [README](README.md). `pytest -q` runs the vision, hydrology and agent tests. The infrastructure tests are under `services/tests` and `infra/test`.

**Where it falls short** is documented in [`docs/report.md`](docs/report.md) §8.3 and §9: hand-held footage with an unstated GRP frame, wind against the surface coefficient, and the drone-footage static-texture diagnostic.
