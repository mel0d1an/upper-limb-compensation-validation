# Annotation procedure

Summary of the instructions given to the two clinicians who produced
`data/annotations.csv`. Translated from the working Russian original.

## The task

Each repetition is judged on its own, as one video segment. For every
repetition the clinician assigns **five independent binary labels** — one per
compensation — and a confidence rating.

Several compensations may be present in one repetition, or none. The five
labels are not mutually exclusive and do not sum to anything.

The threshold for a `1` is clinical, not numerical: the deviation is *clearly
visible* and the clinician would point it out to a patient as a technique error
needing correction. Marginal or doubtful deviations are `0`, with confidence
lowered accordingly.

Confidence: `3` certain, `2` fairly certain, `1` borderline. Disputed cases
could be described in a free-text comment.

## Blinding and independence

The clinicians who annotated were **not** the clinicians who ran the recording
sessions, and they were blind to which condition each repetition had been
instructed as. They worked independently: no discussion with the other
annotator until annotation was complete, and no access to the system's output.

Slow-motion, frame-by-frame and unlimited repeated viewing were permitted.

Crucially, they were asked to mark **every compensation they could see**, not
only the one that had been instructed. The reference labels therefore describe
what is visible in the repetition. This is why a repetition instructed to be
compensation-free may carry a label, and why a flag raised outside the
instructed condition is not a detector error by construction — it may
correspond to a genuine, clinician-labelled co-occurring sign.

## The five compensations

**`elbow` — incomplete elbow extension.** The raise is achieved by bending at
the elbows instead of lifting at the shoulder: at the top of the movement the
arms are visibly bent rather than straight. Physiological micro-flexion does
not count.

**`asymmetry` — inter-limb asymmetry.** The arms move out of step, one visibly
leading the other in height through the raise or the lowering. A brief
divergence at the start, of a fraction of a second, does not count.

**`shoulder` — shoulder-girdle elevation.** The shoulders are drawn towards the
ears during the raise — upper-trapezius participation instead of isolated
movement at the shoulder joint. Visible upward displacement of the shoulders
relative to the starting position, sustained through the raise or the hold.
Natural minimal scapular movement does not count.

**`trunk` — lateral trunk lean.** The trunk tilts to one side during the
movement instead of remaining upright.

**`head` — lateral head tilt.** The head tilts to one side during the movement.

## Agreement

Both clinicians labelled all 621 repetitions of the detection cohort, giving
1242 annotations. They assigned identical labels across all five classes on 477
repetitions; `data/consensus.csv` records which.

The 144 repetitions where they differ are not noise to be discarded. Excluding
them produces a favourable subset — the disagreements concentrate in the two
signs with the lowest inter-rater agreement — which is why the paper's primary
analysis scores against each clinician separately over all 621 repetitions and
reports the consensus figure as an upper bound.
