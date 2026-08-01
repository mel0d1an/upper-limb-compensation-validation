# What is published, what is withheld, and why

## Published

Repetition-level tables: one row per repetition, holding the system's output,
the per-repetition quantities behind it, and the clinicians' labels.
Participants appear only as pseudonymous codes (`P01`, `V03`), and no
per-person demographic attributes are included.

## Withheld

**Video recordings.** Identifiable video of participants. The consent obtained
covers research use and the publication of anonymised results; it does not
cover public release of the recordings. They remain on a restricted laboratory
server under pseudonymous codes, with the code-to-identity key held separately
by the responsible investigator.

**Per-frame keypoint series.** Available from the corresponding author on
request for research use, but not published openly. Two reasons:

1. Derived skeletal coordinates are health data, and the consent contemplates
   publication of aggregated anonymised results rather than continuous
   per-person kinematic records.
2. Continuous whole-body kinematics carry individual movement signatures. The
   paper states plainly that derived keypoints are in principle biometric, and
   that "video never leaves the device" constrains the raw video stream rather
   than certifying that exported derived data are privacy-neutral. Publishing
   those series openly would sit awkwardly with that statement.

The repetition-level tables do not have this property in the same degree: they
are a handful of summary values per repetition, not a trajectory.

**Inertial recordings** from the earlier angular-validation study are not
withheld but simply no longer retained.

## Effect on reproducibility

Deliberately small. The 250 ms decision rule depends on each metric only
through the *sustained value* published for that repetition, so the detector's
output is recoverable at any threshold from the tables alone.
`code/reproduce.py` verifies this against the published flags across all 621
detection repetitions and finds no disagreements, and goes on to reproduce the
per-class scores, the confusion matrices against each clinician, the elbow
criterion, the calibration discrimination and the bootstrap intervals.

What the tables cannot support is re-deriving flags at a different
*trunk-suppression* or *elbow* gate, since those gates change which frames are
eligible to be scored for the asymmetry and head metrics. That analysis needs
the per-frame series.

## Re-identification

The licence on the data permits reuse; it does not permit attempts to
re-identify participants. The consent obtained does not authorise that, and you
must not attempt it.

## Withdrawal

Participants retain the right to withdraw consent and request deletion of their
data. A withdrawal after publication will be honoured in the archived release
by removing the affected participant's rows and issuing a new version; the
version-specific DOI of earlier releases will continue to resolve to their
metadata record.
