#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redraw the elbow-criterion figure from data/repetitions.csv.

Two panels, as in the paper: the per-participant distribution of the minimum
elbow angle reached in each repetition, split by instructed condition, and the
ROC curve for separating the two, with the calibrated operating point marked.

Requires matplotlib.

    python3 figure_elbow.py [--out elbow_threshold.pdf]
"""
import argparse
import csv
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data", "repetitions.csv")
THRESHOLD = 153.4


def load():
    elb, cor = defaultdict(list), defaultdict(list)
    with open(DATA, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["cohort"] != "detection" or not r["elbow_min_deg"]:
                continue
            v = float(r["elbow_min_deg"])
            if r["condition"] == "ELB":
                elb[r["participant_id"]].append(v)
            elif r["condition"] == "COR":
                cor[r["participant_id"]].append(v)
    return elb, cor


def roc(pos, neg):
    """Points of the ROC curve; positives are repetitions with a smaller angle."""
    xs, ys = [], []
    for t in sorted(set(pos + neg)):
        tpr = sum(1 for v in pos if v < t) / len(pos)
        fpr = sum(1 for v in neg if v < t) / len(neg)
        xs.append(fpr)
        ys.append(tpr)
    return [0.0] + xs + [1.0], [0.0] + ys + [1.0]


def auc_rank(pos, neg):
    """Mann-Whitney AUC with mid-ranks for ties; positives score lower."""
    data = sorted([(-v, 1) for v in pos] + [(-v, 0) for v in neg])
    ranks, i = {}, 0
    while i < len(data):
        j = i
        while j + 1 < len(data) and data[j + 1][0] == data[i][0]:
            j += 1
        mid = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks.setdefault(data[k][0], mid)
        i = j + 1
    n1, n0 = len(pos), len(neg)
    rs = sum(ranks[-v] for v in pos)
    return (rs - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "elbow_threshold.pdf"))
    args = ap.parse_args()

    elb, cor = load()
    participants = sorted(set(elb) | set(cor))
    flat_elb = [v for vs in elb.values() for v in vs]
    flat_cor = [v for vs in cor.values() for v in vs]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.2, 3.9))

    # ---- (a) per-participant distribution
    for i, pid in enumerate(participants):
        for v in cor.get(pid, []):
            ax1.plot(v, i, "o", ms=3.4, mfc="none", mec="#4C72B0", mew=0.9, zorder=2)
        for v in elb.get(pid, []):
            ax1.plot(v, i, "o", ms=3.4, color="#C44E52", alpha=0.85, zorder=3)
    ax1.axvline(THRESHOLD, color="k", ls="--", lw=1.1, zorder=4)
    ax1.text(THRESHOLD - 1.5, len(participants) - 0.4, f"{THRESHOLD:.1f}°",
             ha="right", va="top", fontsize=8.5)
    ax1.set_yticks(range(len(participants)))
    ax1.set_yticklabels(participants, fontsize=7)
    ax1.set_xlabel("Minimum elbow angle over the repetition (deg)")
    ax1.set_ylabel("Participant")
    ax1.set_title("(a) Per-participant distribution", fontsize=10)
    ax1.plot([], [], "o", ms=4, mfc="none", mec="#4C72B0", label="instructed correct")
    ax1.plot([], [], "o", ms=4, color="#C44E52", label="instructed elbow flexion")
    ax1.legend(fontsize=7.5, loc="lower left", framealpha=0.9)
    ax1.grid(axis="x", alpha=0.25, lw=0.6)
    ax1.set_axisbelow(True)

    # ---- (b) ROC
    xs, ys = roc(flat_elb, flat_cor)
    a = auc_rank(flat_elb, flat_cor)
    ax2.plot(xs, ys, "-", color="#C44E52", lw=1.6, zorder=3)
    ax2.plot([0, 1], [0, 1], ":", color="0.55", lw=1.0, zorder=1)
    sens = sum(1 for v in flat_elb if v < THRESHOLD) / len(flat_elb)
    spec = sum(1 for v in flat_cor if v >= THRESHOLD) / len(flat_cor)
    ax2.plot(1 - spec, sens, "o", ms=7, mfc="none", mec="k", mew=1.4, zorder=4)
    ax2.annotate(f"operating point\n{THRESHOLD:.1f}°  "
                 f"(sens {sens:.2f}, spec {spec:.2f})",
                 xy=(1 - spec, sens), xytext=(0.30, 0.42), fontsize=8,
                 arrowprops=dict(arrowstyle="->", lw=0.9, color="k"))
    ax2.text(0.96, 0.06, f"AUC = {a:.3f}", ha="right", fontsize=9.5)
    ax2.set_xlabel("False-positive rate")
    ax2.set_ylabel("Sensitivity")
    ax2.set_title("(b) ROC, incomplete elbow extension", fontsize=10)
    ax2.set_xlim(-0.02, 1.02)
    ax2.set_ylim(-0.02, 1.02)
    ax2.grid(alpha=0.25, lw=0.6)
    ax2.set_axisbelow(True)

    fig.tight_layout()
    fig.savefig(args.out, bbox_inches="tight")
    print(f"written: {args.out}")
    print(f"  n(elbow flexion) = {len(flat_elb)}, n(correct) = {len(flat_cor)}, "
          f"AUC = {a:.3f}, sensitivity = {sens:.2f}, specificity = {spec:.2f}")


if __name__ == "__main__":
    main()
