# Evaluation

## Video Globe Challenge 2020

Eight real videos from France, Norway and Australia (five ground cameras, three drones). Each comes with a reference discharge (mostly ADCP), its uncertainty, and the answers of 15–23 hydrologists who processed the same footage (Bodart et al. 2023; doi:10.57745/KVNIHX).

**Protocol**
1. Videos 1 and 8 were used for development.
2. Station configs for videos 2–7 were built only from survey inputs: GRPs, bathymetry, water level and site notes. No results file was opened.
3. The agent, policy and parameters were frozen (`git tag eval-freeze`).
4. Videos 2–7 were scored once per brain:
   ```
   python eval/make_keys.py                 # answer keys -> eval/keys/ (git-ignored; only the scorer reads them)
   python eval/score_vgc.py --brain autopilot --videos 1 8 2 3 4 5 6 7 --tag vgc-autopilot
   python eval/score_vgc.py --brain llm --model claude-sonnet-5-5 --videos 1 8 2 3 4 5 6 7 --tag vgc-llm-sonnet
   python eval/report.py
   ```
5. Every run, of any version, is appended to `runs.jsonl`. Results are under `results/<tag>/`: a summary plus the full trace of every run.

Runs tagged `-v1.1` came after fixes prompted by the refusal suite. They are **not** blind and are reported next to the frozen runs, never in place of them.

**Metrics** (per video)
- signed error of the published discharge and of the best unpublished estimate;
- whether the published value falls within the reference uncertainty;
- rank among the experts, by absolute error;
- the share of experts beaten.

## Refusal suite

`refusal_suite.py build` corrupts clips from videos 1 and 8 in known ways and writes the expected decision for each case. `refusal_suite.py run --brain ...` scores the decisions. A wrong `PUBLISH` on a must-refuse case is counted separately; it is the failure that matters.

| Case | Transform | Expected |
|---|---|---|
| clean_vgc1, clean_vgc8 | none | PUBLISH |
| handheld_shake_vgc8 | AR(1) jitter, several px per frame | PUBLISH |
| night_vgc1 | gain ×0.07 + noise | HOLD |
| glare_vgc8 | 55% of the frame saturated | HOLD |
| frozen_feed_vgc1 | one frame repeated | HOLD |
| still_water_vgc8 | one frame + sensor noise | HOLD |
| camera_moved_vgc1 | 28 / −18 px shift vs the reference view | ESCALATE |
| level_above_banks_vgc8 | sensor level +1.5 m | ESCALATE |
