# compensation-study-tools

Tools of the validation study: detection of upper-limb compensatory movements
against blind clinician annotation. Companion repository to the MDPI Sensors
paper (the manuscript lives in a separate private repository
`sensors-compensation-paper`; there the experiment closes the
"Compensation Detection vs. Clinician Annotation" subsection and provides the
data for the Threshold Sensitivity Analysis).

## Package contents

| File | Purpose |
|---|---|
| `protocol.md` | Study protocol: design, participants, recording procedure, data |
| `annotator_instructions.md` | Instructions for the annotating clinicians (criteria for the 5 compensations) |
| `consent_form.md` | Informed-consent template (adapt to the ethics committee's requirements) |
| `templates/system_predictions.csv` | Sample format of the system-prediction export |
| `analysis.py` | Analysis script: Cohen's kappa, consensus, P/R/F1 + bootstrap CIs, sensitivity |
| `webapp/` | Study application: recording with upload to the server + blind-annotation workspace for the clinicians + CSV export; `webapp/recompute.py` — canonical recomputation of predictions from keypoints (see `webapp/README.md`) |

## Workflow

1. **Ethics.** Check whether the current ethics approval covers video recording
   of volunteers; file an amendment if needed. Prepare the consent forms
   (`consent_form.md`).
2. **Engineering setup.** Recording and annotation run through `webapp/`
   (Flask + MediaPipe Pose in the browser, on-device processing; deployment —
   `webapp/README.md`). Per block, the app stores the `.webm` video, the
   `.jsonl` keypoints and the system predictions (with repetition boundaries).
   Detection thresholds are fixed in the `THR` constant
   (`webapp/static/record.html`) and do not change during data collection;
   they are calibrated beforehand on a pilot/reference set (see below on
   `recompute.py`) and frozen before the validation starts.
3. **Recording.** 12–20 participants following the protocol (`protocol.md`).
   One participant takes ~20–25 minutes.
4. **Annotation.** Two clinicians independently, blind to the system output,
   following `annotator_instructions.md`: they log into the web workspace
   (`webapp/`) with individual tokens and annotate clip by clip; the researcher
   exports the finished `annotations_R1.csv`/`annotations_R2.csv`.
5. **Analysis.**
   ```bash
   # system predictions — canonical recomputation from keypoints (a stable
   # resting "zero", independent of the quality of the live calibration):
   python3 webapp/recompute.py --data webapp/data --out system_predictions.csv

   python3 analysis.py kappa rater1.csv rater2.csv
   python3 analysis.py consensus rater1.csv rater2.csv -o consensus.csv
   # resolve disputed cases (adjudication_needed=1) with a third rater
   # or a consensus discussion, enter the values into consensus.csv
   python3 analysis.py evaluate consensus.csv system_predictions.csv
   python3 analysis.py sensitivity consensus.csv "predictions_thr*.csv"
   ```
6. Transfer the results into `sections/04_results.tex` (table `tab:detection`,
   kappa into the text, confusion matrix — a figure).

## Key principles (do not violate)

- **Detection thresholds are frozen before the validation starts.** They may be
  calibrated on a separate pilot/reference set (clinical reference videos) —
  but NOT on the same participants the validation is later computed on: tuning
  thresholds to the validation data invalidates the result (circular
  validation). The threshold version is recorded in the prediction export.
- **Annotators do not see the system output** and annotate by clinical
  criteria, not by the algorithm's numeric thresholds (otherwise circular
  validation).
- **Confidence intervals — bootstrap over participants**, not over repetitions
  (repetitions of one person are correlated).
- Keypoints are always stored: the sensitivity analysis and any re-analyses
  are done offline without collecting data again.
