# Recording protocol

## Exercise

Seated lateral arm raise: from arms resting at the sides, both arms are raised
through the frontal plane and lowered again. The instruction targets roughly
90° of elevation, demonstrated rather than measured.

Participants did not in practice reach the horizontal. Elevation plateaued
around 61°, so the range actually covered by the recordings is 0–62°, and the
paper limits its claims accordingly.

## Conditions

Six conditions per participant, one recording block each:

| Code | Instruction |
|---|---|
| `COR` | Perform the movement correctly, without compensation |
| `ELB` | Do not fully extend the elbow |
| `ASY` | Raise one arm noticeably higher than the other |
| `SHR` | Shrug — elevate the shoulders while raising the arms |
| `TRK` | Lean the trunk to one side |
| `HED` | Tilt the head to one side |

Before each condition a clinician both instructed the participant and
physically demonstrated the target movement and the compensation to be
reproduced. Amplitude was therefore set by demonstration, not by an
instrumented target.

Condition order was randomised between participants.

Participants were **not** asked to isolate the instructed compensation.
Naturally co-occurring compensations were permitted rather than suppressed,
which is why the annotation task allows several labels on one repetition.

Each block contains several repetitions; repetition boundaries were produced by
the pipeline's segmentation state machine.

## Cohorts

Three non-overlapping groups of healthy adults, 37 unique participants in
total:

| Cohort | n | Purpose | In this release |
|---|---|---|---|
| Angular validation | 10 | Comparison against an inertial reference | No — raw recordings not retained |
| Calibration | 9 | Fixing the detection thresholds | Yes, 292 repetitions |
| Detection | 18 | Evaluating the detector against clinician labels | Yes, 621 repetitions |

Aggregate demographics across the cohorts: age 24.0 ± 4.3 years (median 24,
range 18–32), approximately 75% male in each cohort, stature roughly 165–190 cm,
mass roughly 60–120 kg.

Stature and mass are given as approximate ranges because anthropometry was not
systematically measured. Handedness was not recorded either. Both gaps are
stated as limitations in the paper; the consequence for the inter-limb
asymmetry metric is that a systematic difference between the dominant and
non-dominant limb would present as asymmetry and cannot be separated from the
simulated compensation on these data.

## Capture

A single consumer webcam facing the seated participant, requested at
1280×720 at 30 frames per second, in one fixed configuration. Camera height,
distance, viewing angle and lighting were not varied across the study, so the
dataset characterises one controlled configuration rather than a range of home
conditions.

Pose estimation ran in the browser: MediaPipe Tasks Vision 0.10.14,
`pose_landmarker_full` (float16), VIDEO mode with a single pose, GPU (WebGL)
delegate over the WebAssembly runtime. Per-device operating-system and browser
versions, the resolution the webcam actually delivered, and whether the
requested GPU delegate was in fact granted were not recorded.

## Geometry

Landmark coordinates arrive normalised per axis (`x = px/W`, `y = px/H`). On a
16:9 frame this scales the axes differently, so in-plane angles are not
preserved and any ratio mixing vertical and horizontal distances depends on the
aspect ratio. All geometry in this release therefore multiplies `x` by `W/H`
first, which is equivalent to working in pixel coordinates up to a global
scale.

The resting baseline — shoulder height, shoulder width and head tilt — is taken
as the median over rest-phase frames with all reference landmarks in frame,
rather than over the opening seconds of the recording, which can catch the
participant still settling.

A flag is raised when a violation accumulates 250 ms within a repetition.
Elapsed time is measured from per-frame timestamps and capped at 100 ms per
gap, so a dropped detection cannot inflate it, and a repetition is scored the
same at 22 and at 30 frames per second.
