# Data dictionary

Five tables. `clip_id` is the join key throughout and has the form
`<participant>_<condition>_rep<n>`, for example `P07_SHR_rep3`.

Condition codes: `COR` instructed to be compensation-free, `ELB` incomplete
elbow extension, `ASY` inter-limb asymmetry, `SHR` shoulder elevation,
`TRK` trunk lean, `HED` head tilt.

Compensation classes, used as column names in several tables: `elbow`,
`asymmetry`, `shoulder`, `trunk`, `head`.

---

## `repetitions.csv` — 913 rows, one per repetition

The unit of analysis. Covers both cohorts: 621 repetitions from the 18
detection participants and 292 from the 9 calibration participants, which do
not overlap.

| Column | Type | Description |
|---|---|---|
| `clip_id` | string | Join key. |
| `participant_id` | string | Pseudonymous code. `P*` detection cohort, `V*` calibration cohort. |
| `cohort` | string | `detection` or `calibration`. |
| `condition` | string | Which condition the participant was instructed to perform. |
| `repetition` | integer | Repetition number within the recording block. |
| `pred_elbow` … `pred_head` | 0/1 | System output at the calibrated thresholds. These are the values scored in the paper. |
| `elbow_sustained_deg` | degrees | See *Sustained values* below. |
| `asymmetry_sustained_deg` | degrees | idem |
| `shoulder_sustained` | shoulder widths | idem |
| `trunk_sustained_rad` | radians | idem |
| `head_sustained_deg` | degrees | idem |
| `elbow_min_deg` | degrees | Smallest elbow angle of the two arms at any point in the repetition. |
| `asymmetry_max_deg` | degrees | Largest inter-limb difference in arm elevation, ungated. |
| `shoulder_max` | shoulder widths | Largest shoulder rise above the resting baseline. |
| `trunk_max_rad` | radians | Largest trunk tilt from vertical. |
| `head_max_deg` | degrees | Largest lateral head tilt relative to the resting baseline. |
| `n_frames` | integer | Frames inside the repetition with all reference landmarks in frame. |
| `duration_ms` | integer | Elapsed time over those frames, capped per gap at 100 ms. |
| `fps` | float | Nominal capture rate of the recording block. |

### Sustained values

A flag is raised when a violation *persists* for at least 250 ms within the
repetition, not when it occurs in a single frame. The `*_sustained_*` columns
record, for each metric, the value that the 250 ms rule actually turns on:

> the most extreme value `v` such that the time spent at or beyond `v` within
> the repetition reaches 250 ms.

This is the sufficient statistic for the decision rule. The flag at any
threshold `T` is exactly

* `elbow`: raised when `elbow_sustained_deg < T` (fires below the threshold);
* the other four: raised when the sustained value `>= T`.

So a reader can move any threshold and recover the flags that would have been
produced, without needing the per-frame series. `code/reproduce.py` checks this
identity against the published flags on all 621 detection repetitions; it holds
exactly.

Two caveats. The asymmetry and head metrics are only scored on frames where the
trunk is below the suppression tilt (asymmetry additionally requires the elbow
to be extended), and the sustained values are computed under the published
gate constants. Re-deriving flags at a different *trunk-suppression* or *elbow*
threshold therefore changes which frames are eligible and is not reproducible
from this table alone. Sweeps over the asymmetry, shoulder, trunk and head
thresholds themselves are exact.

An empty cell means the metric never accumulated 250 ms in that repetition
under its gates; the flag is 0 by definition.

The `*_max` and `*_min` columns are plain extrema over the same frames, with no
gating and no duration requirement. They describe the repetition; they are not
what the detector thresholds. Figure 4 of the paper uses `elbow_min_deg`.

---

## `annotations.csv` — 1242 rows

Independent labels from the two clinicians, covering every one of the 621
detection repetitions twice.

| Column | Type | Description |
|---|---|---|
| `clip_id` | string | Join key. |
| `rater` | string | `R1` or `R2`. |
| `elbow` … `head` | 0/1 | Whether that clinician saw the sign in that repetition. |
| `confidence` | 1–3 | The clinician's own certainty, 3 highest. |

The clinicians labelled **every visible sign**, not only the instructed one, and
were blind to the instructed condition. A repetition instructed to be
compensation-free may therefore carry a label, and a flag raised outside the
instructed condition is not a detector error by construction.

## `consensus.csv` — 621 rows

| Column | Type | Description |
|---|---|---|
| `clip_id` | string | Join key. |
| `agreed` | 0/1 | 1 when the two clinicians assigned identical labels across all five classes. |
| `elbow` … `head` | 0/1 or empty | The agreed labels; empty when `agreed = 0`. |

477 of the 621 repetitions were fully agreed. Scoring on this subset is the
favourable reading and is reported in the paper as an upper bound; the primary
result is scored against each clinician separately over all 621.

## `participants.csv` — 27 rows

| Column | Type | Description |
|---|---|---|
| `participant_id` | string | Pseudonymous code. |
| `cohort` | string | `detection` or `calibration`. |
| `conditions` | integer | Number of distinct conditions recorded. |
| `repetitions` | integer | Number of repetitions contributed. |

Deliberately carries no per-person demographics. Age, sex, stature and mass are
reported in the paper only as cohort-level summaries, and anthropometry and
handedness were never measured — a limitation stated in the paper, not an
omission from this release.

## `thresholds.csv` — 7 rows

The calibrated operating points, the gate that suppresses the asymmetry and
head metrics under trunk lean, and the 250 ms violation window. `direction`
says whether the metric fires above or below its threshold.
