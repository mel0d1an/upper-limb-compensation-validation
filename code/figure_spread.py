#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""How unevenly the detector performs — across participants and across signs.

Two panels. Left: macro-F1 for each participant separately, which the single
macro-average in the abstract does not show. Right: per-class F1 against each
clinician with participant-clustered bootstrap intervals, which is the evidence
behind the paper's three tiers of maturity.

Scoring is imported from reproduce.py, so these panels cannot drift away from
the numbers that script verifies against the manuscript.

The rendered output is committed under `figures/`. Requires matplotlib.

    python3 figure_spread.py [--out ../figures/spread]
"""
import argparse
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reproduce as R  # noqa: E402

BLUE, RED, GREY = "#4C72B0", "#C44E52", "#8C8C8C"
LABEL = {"elbow": "incomplete elbow\nextension", "asymmetry": "inter-limb\nasymmetry",
         "shoulder": "shoulder\nelevation", "trunk": "trunk lean", "head": "head tilt"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "figures", "spread"))
    ap.add_argument("--iterations", type=int, default=10000)
    args = ap.parse_args()
    base = os.path.splitext(args.out)[0]
    os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)

    reps, ann, consensus = R.load()
    detection = [r for r in reps if r["cohort"] == "detection"]
    agreed = [r for r in detection
              if consensus.get(r["clip_id"], {}).get("agreed") == "1"]

    # ---- (a) macro-F1 per participant, scored against the consensus labels
    by_participant = defaultdict(list)
    for r in agreed:
        by_participant[r["participant_id"]].append(r)
    per_p = {}
    for pid, rows in by_participant.items():
        pairs = [(R.flags_at(r, R.THRESHOLDS),
                  {c: int(consensus[r["clip_id"]][c]) for c in R.CLASSES}) for r in rows]
        per_p[pid] = R.score(pairs)["macro_f1"]
    order = sorted(per_p, key=per_p.get)
    vals = [per_p[p] for p in order]
    med = R.median_of(vals)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.4, 4.2),
                                   gridspec_kw={"width_ratios": [1, 1.15]})

    ys = range(len(order))
    ax1.hlines(list(ys), 0, vals, color="#C9CFD8", lw=2.2, zorder=1)
    ax1.plot(vals, list(ys), "o", ms=6, color=BLUE, zorder=3)
    ax1.axvline(med, color=RED, ls="--", lw=1.2, zorder=2)
    ax1.text(med + 0.012, -0.85, f"median {med:.2f}", color=RED, fontsize=8.5, va="bottom")
    ax1.set_yticks(list(ys))
    ax1.set_yticklabels(order, fontsize=7.5)
    ax1.set_xlim(0, 1.0)
    ax1.set_ylim(-1.4, len(order) - 0.4)
    ax1.set_xlabel("macro-F1 for that participant")
    ax1.set_ylabel("Participant")
    ax1.set_title("(a) One number per participant", fontsize=10)
    ax1.grid(axis="x", alpha=0.25, lw=0.6)
    ax1.set_axisbelow(True)

    # ---- (b) per-class F1 against each clinician, with bootstrap intervals
    pairs_by_p = {rater: defaultdict(list) for rater in ("R1", "R2")}
    for r in detection:
        for rater in ("R1", "R2"):
            if rater in ann[r["clip_id"]]:
                pairs_by_p[rater][r["participant_id"]].append(
                    (R.flags_at(r, R.THRESHOLDS), ann[r["clip_id"]][rater]))

    scores = {}
    for rater in ("R1", "R2"):
        pooled = [p for ps in pairs_by_p[rater].values() for p in ps]
        scores[rater] = R.score(pooled)

    classes = sorted(R.CLASSES, key=lambda c: scores["R1"][c]["f1"])
    offsets = {"R1": -0.16, "R2": 0.16}
    colours = {"R1": BLUE, "R2": RED}
    for rater in ("R1", "R2"):
        xs, los, his = [], [], []
        for c in classes:
            lo, hi = R.cluster_bootstrap(
                detection, pairs_by_p[rater],
                lambda ps, cc=c: R.score(ps)[cc]["f1"], args.iterations, R.BOOTSTRAP_SEED)
            xs.append(scores[rater][c]["f1"])
            los.append(lo)
            his.append(hi)
        ys2 = [i + offsets[rater] for i in range(len(classes))]
        ax2.hlines(ys2, los, his, color=colours[rater], lw=1.6, alpha=0.75, zorder=2)
        ax2.plot(xs, ys2, "o", ms=6, color=colours[rater], zorder=3,
                 label=f"vs clinician {rater[-1]}")

    ax2.set_yticks(range(len(classes)))
    ax2.set_yticklabels([LABEL[c] for c in classes], fontsize=8.5)
    ax2.set_xlim(0, 1.0)
    ax2.set_ylim(-0.6, len(classes) - 0.4)
    ax2.set_xlabel("F1 over all 621 repetitions, 95% CI")
    ax2.set_title("(b) One number per sign", fontsize=10)
    ax2.legend(fontsize=8, loc="lower right", framealpha=0.92)
    ax2.grid(axis="x", alpha=0.25, lw=0.6)
    ax2.set_axisbelow(True)

    # the tiers the paper draws, marked where they fall
    for y, text in ((0, "research-grade"), (1.5, "moderate"), (3.5, "near-expert")):
        ax2.text(0.035, y, text, fontsize=7.5, style="italic", color=GREY,
                 va="center")

    fig.tight_layout()
    for ext, kw in (("pdf", {}), ("png", {"dpi": 200})):
        path = f"{base}.{ext}"
        fig.savefig(path, bbox_inches="tight", facecolor="white", **kw)
        print(f"written: {path}")
    print(f"  participant macro-F1: min {min(vals):.2f}, median {med:.2f}, "
          f"max {max(vals):.2f}  (n = {len(vals)})")
    for c in classes:
        print(f"  {c:<10} F1 vs R1 {scores['R1'][c]['f1']:.3f}   "
              f"vs R2 {scores['R2'][c]['f1']:.3f}")


if __name__ == "__main__":
    main()
