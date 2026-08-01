#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Confusion matrices against each clinician separately, class by class.

Ten 2x2 matrices: five compensation signs, scored against each of the two
clinicians over all 621 repetitions. Cells are shaded by proportion within the
clinician's row, so the shading reads as recall and specificity and is not
swamped by the true negatives; the raw counts are printed regardless.

Scoring is imported from reproduce.py, so these matrices cannot drift away from
the numbers that script verifies against the manuscript.

The rendered output is committed under `figures/`. Requires matplotlib.

    python3 figure_confusion.py [--out ../figures/confusion]
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import reproduce as R  # noqa: E402

TITLE = {"elbow": "incomplete elbow extension", "asymmetry": "inter-limb asymmetry",
         "shoulder": "shoulder elevation", "trunk": "trunk lean", "head": "head tilt"}
CMAP = LinearSegmentedColormap.from_list("agree", ["#FFFFFF", "#4C72B0"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "figures", "confusion"))
    args = ap.parse_args()
    base = os.path.splitext(args.out)[0]
    os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)

    reps, ann, _ = R.load()
    detection = [r for r in reps if r["cohort"] == "detection"]

    fig, axes = plt.subplots(2, 5, figsize=(12.6, 6.1))
    for row, rater in enumerate(("R1", "R2")):
        pairs = [(R.flags_at(r, R.THRESHOLDS), ann[r["clip_id"]][rater])
                 for r in detection if rater in ann[r["clip_id"]]]
        s = R.score(pairs)
        for col, c in enumerate(R.CLASSES):
            ax = axes[row][col]
            tp, fp, fn, tn = s[c]["tp"], s[c]["fp"], s[c]["fn"], s[c]["tn"]
            # rows are the clinician's label, columns the system's output
            counts = [[tn, fp], [fn, tp]]
            totals = [tn + fp, fn + tp]
            shade = [[(counts[i][j] / totals[i] if totals[i] else 0) for j in range(2)]
                     for i in range(2)]
            ax.imshow(shade, cmap=CMAP, vmin=0, vmax=1)
            for i in range(2):
                for j in range(2):
                    ax.text(j, i, f"{counts[i][j]}", ha="center", va="center",
                            fontsize=11,
                            color="white" if shade[i][j] > 0.55 else "#222222")
            ax.set_xticks([0, 1])
            ax.set_yticks([0, 1])
            ax.tick_params(length=0)
            for spine in ax.spines.values():
                spine.set_edgecolor("#BBBBBB")

            f1 = s[c]["f1"]
            if row == 0:
                ax.set_title(f"{TITLE[c]}\nF1 {f1:.2f}", fontsize=9, pad=7)
                ax.set_xticklabels([])
            else:
                ax.set_title(f"F1 {f1:.2f}", fontsize=9, color="#555555", pad=7)
                ax.set_xticklabels(["absent", "flagged"], fontsize=8)
                ax.set_xlabel("system output", fontsize=9)
            if col == 0:
                ax.set_yticklabels(["absent", "present"], fontsize=8)
                ax.set_ylabel(f"clinician {rater[-1]}\nlabelled", fontsize=9)
            else:
                ax.set_yticklabels([])

    fig.suptitle("Agreement with each clinician over all 621 repetitions, "
                 "shaded within the clinician's row", fontsize=10.5, y=0.98)
    fig.tight_layout(rect=(0, 0, 1, 0.955), h_pad=3.2)
    for ext, kw in (("pdf", {}), ("png", {"dpi": 200})):
        path = f"{base}.{ext}"
        fig.savefig(path, bbox_inches="tight", facecolor="white", **kw)
        print(f"written: {path}")

    for rater in ("R1", "R2"):
        pairs = [(R.flags_at(r, R.THRESHOLDS), ann[r["clip_id"]][rater])
                 for r in detection if rater in ann[r["clip_id"]]]
        s = R.score(pairs)
        cells = "  ".join(f"{c} {s[c]['tp']}/{s[c]['fp']}/{s[c]['fn']}/{s[c]['tn']}"
                          for c in R.CLASSES)
        print(f"  {rater} TP/FP/FN/TN — {cells}")


if __name__ == "__main__":
    main()
