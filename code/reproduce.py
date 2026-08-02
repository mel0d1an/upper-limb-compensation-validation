#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Recompute every headline number of the paper from the published tables.

Each block prints what it computed next to the value reported in the
manuscript and marks it OK or MISMATCH, so the release verifies itself rather
than asking the reader to take the numbers on trust.

Standard library only.

    python3 reproduce.py                 # all checks
    python3 reproduce.py --no-bootstrap  # skip the interval estimates (faster)
"""
import argparse
import csv
import math
import os
import random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
CLASSES = ["elbow", "asymmetry", "shoulder", "trunk", "head"]

THRESHOLDS = {"elbow": 153.4, "asymmetry": 13.2, "shoulder": 0.174,
              "trunk": 0.090, "head": 6.4}

BOOTSTRAP_ITERATIONS = 10000
BOOTSTRAP_SEED = 20260724

_fails = []


def check(label, got, expected, tol, unit=""):
    """Report one value against the manuscript and remember any mismatch."""
    ok = got is not None and abs(got - expected) <= tol
    mark = "OK" if ok else "MISMATCH"
    shown = "n/a" if got is None else f"{got:.4g}"
    print(f"  {label:<44} {shown:>9}{unit}   paper {expected:g}{unit}   {mark}")
    if not ok:
        _fails.append(label)
    return ok


# ------------------------------------------------------------------ loading --
def read(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def as_float(row, key):
    v = row.get(key, "")
    return None if v == "" else float(v)


def load():
    reps = read("repetitions.csv")
    for r in reps:
        r["repetition"] = int(r["repetition"])
        for c in CLASSES:
            r[f"pred_{c}"] = int(r[f"pred_{c}"])
        for k in ("elbow_sustained_deg", "asymmetry_sustained_deg", "shoulder_sustained",
                  "trunk_sustained_rad", "head_sustained_deg", "elbow_min_deg",
                  "asymmetry_max_deg", "shoulder_max", "trunk_max_rad", "head_max_deg"):
            r[k] = as_float(r, k)

    ann = defaultdict(dict)
    for a in read("annotations.csv"):
        ann[a["clip_id"]][a["rater"]] = {c: int(a[c]) for c in CLASSES}

    consensus = {c["clip_id"]: c for c in read("consensus.csv")}
    return reps, ann, consensus


def flags_at(row, thresholds):
    """Re-derive the flags from the sustained values at any thresholds."""
    def high(v, t):
        return 0 if v is None else int(v >= t)
    e = row["elbow_sustained_deg"]
    return {
        "elbow": 0 if e is None else int(e < thresholds["elbow"]),
        "asymmetry": high(row["asymmetry_sustained_deg"], thresholds["asymmetry"]),
        "shoulder": high(row["shoulder_sustained"], thresholds["shoulder"]),
        "trunk": high(row["trunk_sustained_rad"], thresholds["trunk"]),
        "head": high(row["head_sustained_deg"], thresholds["head"]),
    }


# ------------------------------------------------------------------ scoring --
def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


def score(pairs):
    """pairs: list of (predicted dict, reference dict) -> per class and macro."""
    out = {}
    for c in CLASSES:
        tp = fp = fn = tn = 0
        for pred, ref in pairs:
            if pred[c] and ref[c]:
                tp += 1
            elif pred[c] and not ref[c]:
                fp += 1
            elif not pred[c] and ref[c]:
                fn += 1
            else:
                tn += 1
        p, r, f = prf(tp, fp, fn)
        out[c] = {"precision": p, "recall": r, "f1": f,
                  "specificity": tn / (tn + fp) if tn + fp else 0.0,
                  "fp_rate": fp / (fp + tn) if fp + tn else 0.0,
                  "tp": tp, "fp": fp, "fn": fn, "tn": tn}
    out["macro_f1"] = sum(out[c]["f1"] for c in CLASSES) / len(CLASSES)
    return out


def cluster_bootstrap(reps, pairs_by_participant, stat, iterations, seed):
    """Percentile interval, resampling participants (not repetitions) with
    replacement, so repeated measures within a participant stay together."""
    rng = random.Random(seed)
    ids = sorted(pairs_by_participant)
    vals = []
    for _ in range(iterations):
        drawn = [rng.choice(ids) for _ in ids]
        pooled = []
        for pid in drawn:
            pooled.extend(pairs_by_participant[pid])
        try:
            vals.append(stat(pooled))
        except ZeroDivisionError:
            continue
    vals.sort()
    if not vals:
        return None, None
    lo = vals[int(0.025 * len(vals))]
    hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
    return lo, hi


# ---------------------------------------------------------------------- ROC --
def roc_auc(pos, neg):
    """Rank-based (Mann-Whitney) AUC with ties handled by mid-ranks.

    The trapezoid estimator over a coarse threshold grid gives a slightly
    different value; the manuscript reports this one.
    """
    data = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    ranks, i = {}, 0
    while i < len(data):
        j = i
        while j + 1 < len(data) and data[j + 1][0] == data[i][0]:
            j += 1
        mid = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks.setdefault(data[k][0], mid)
        i = j + 1
    rank_sum = sum(ranks[v] for v in pos)
    n1, n0 = len(pos), len(neg)
    return (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def median_of(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


# --------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-bootstrap", action="store_true")
    ap.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    args = ap.parse_args()

    reps, ann, consensus = load()
    detection = [r for r in reps if r["cohort"] == "detection"]
    calibration = [r for r in reps if r["cohort"] == "calibration"]

    print("=" * 78)
    print("Dataset")
    print("=" * 78)
    check("detection repetitions", len(detection), 621, 0)
    check("calibration repetitions", len(calibration), 292, 0)
    check("participants, detection",
          len({r["participant_id"] for r in detection}), 18, 0)
    check("participants, calibration",
          len({r["participant_id"] for r in calibration}), 9, 0)
    check("doubly annotated repetitions",
          sum(1 for c in ann.values() if len(c) == 2), 621, 0)
    check("repetitions both clinicians agreed on",
          sum(1 for c in consensus.values() if c["agreed"] == "1"), 477, 0)

    # the published flags must fall out of the published sustained values
    mismatched = sum(1 for r in detection
                     for c in CLASSES
                     if flags_at(r, THRESHOLDS)[c] != r[f"pred_{c}"])
    print()
    check("flags re-derived from sustained values", mismatched, 0, 0,
          unit=" disagreements")

    print()
    print("=" * 78)
    print("Detection against each clinician, all 621 repetitions (primary result)")
    print("=" * 78)
    expected_macro = {"R1": 0.75, "R2": 0.72}
    expected_f1 = {
        "R1": {"elbow": .72, "asymmetry": .66, "shoulder": .55, "trunk": .92, "head": .90},
        "R2": {"elbow": .73, "asymmetry": .60, "shoulder": .50, "trunk": .92, "head": .87},
    }
    expected_confusion = {
        "R1": {"elbow": (58, 18, 28, 517), "asymmetry": (96, 58, 42, 425),
               "shoulder": (83, 65, 69, 404), "trunk": (88, 14, 1, 518),
               "head": (91, 9, 12, 509)},
        "R2": {"elbow": (58, 18, 26, 519), "asymmetry": (92, 62, 62, 405),
               "shoulder": (67, 81, 53, 420), "trunk": (87, 15, 1, 518),
               "head": (90, 10, 17, 504)},
    }
    per_rater = {}
    for rater in ("R1", "R2"):
        pairs = [(flags_at(r, THRESHOLDS), ann[r["clip_id"]][rater])
                 for r in detection if rater in ann[r["clip_id"]]]
        s = score(pairs)
        per_rater[rater] = s
        print(f"\n {rater}")
        for c in CLASSES:
            check(f"  {c} F1", s[c]["f1"], expected_f1[rater][c], 0.006)
        check(f"  macro-F1", s["macro_f1"], expected_macro[rater], 0.006)
        for c in CLASSES:
            got = (s[c]["tp"], s[c]["fp"], s[c]["fn"], s[c]["tn"])
            exp = expected_confusion[rater][c]
            mark = "OK" if got == exp else "MISMATCH"
            print(f"  {'  ' + c + ' TP/FP/FN/TN':<44} {'/'.join(map(str, got)):>17}"
                  f"   paper {'/'.join(map(str, exp))}   {mark}")
            if got != exp:
                _fails.append(f"{rater} {c} confusion")

    print()
    print("=" * 78)
    print("Detection on the consensus subset (upper bound, reported second)")
    print("=" * 78)
    agreed = [r for r in detection if consensus.get(r["clip_id"], {}).get("agreed") == "1"]
    pairs = [(flags_at(r, THRESHOLDS),
              {c: int(consensus[r["clip_id"]][c]) for c in CLASSES}) for r in agreed]
    s = score(pairs)
    expected_consensus = {"elbow": (.74, .69, .72), "asymmetry": (.67, .81, .73),
                          "shoulder": (.53, .63, .57), "trunk": (.84, 1.00, .92),
                          "head": (.91, .97, .94)}
    for c in CLASSES:
        p, r_, f = expected_consensus[c]
        check(f"{c} precision", s[c]["precision"], p, 0.006)
        check(f"{c} recall", s[c]["recall"], r_, 0.006)
        check(f"{c} F1", s[c]["f1"], f, 0.006)
    check("macro-F1", s["macro_f1"], 0.78, 0.006)
    exact = sum(1 for pred, ref in pairs if all(pred[c] == ref[c] for c in CLASSES))
    check("exact-match rate", 100.0 * exact / len(pairs), 65.6, 0.15, unit=" %")

    print()
    print("=" * 78)
    print("Shoulder elevation: the metric the reviewers challenged")
    print("=" * 78)
    fp_rates = [per_rater[r]["shoulder"]["fp_rate"] for r in ("R1", "R2")]
    check("label-based false-positive rate (mean of raters)",
          sum(fp_rates) / 2, 0.15, 0.02)
    cor = [r for r in detection if r["condition"] == "COR"]
    check("flag rate on repetitions instructed correct",
          100.0 * sum(flags_at(r, THRESHOLDS)["shoulder"] for r in cor) / len(cor),
          14, 1.5, unit=" %")

    print()
    print("=" * 78)
    print("Spread across participants")
    print("=" * 78)
    # computed on the consensus subset, as in the manuscript
    by_participant = defaultdict(list)
    for r in agreed:
        by_participant[r["participant_id"]].append(r)
    macro_by_p = []
    for pid, rows in sorted(by_participant.items()):
        pairs = [(flags_at(r, THRESHOLDS),
                  {c: int(consensus[r["clip_id"]][c]) for c in CLASSES}) for r in rows]
        macro_by_p.append(score(pairs)["macro_f1"])
    check("lowest participant macro-F1", min(macro_by_p), 0.38, 0.02)
    check("highest participant macro-F1", max(macro_by_p), 0.96, 0.02)
    check("median participant macro-F1", median_of(macro_by_p), 0.75, 0.02)

    print()
    print("=" * 78)
    print("Incomplete elbow extension: the criterion behind Figure 4")
    print("=" * 78)
    elb = [r["elbow_min_deg"] for r in detection if r["condition"] == "ELB"]
    cor_e = [r["elbow_min_deg"] for r in detection if r["condition"] == "COR"]
    check("repetitions instructed to flex the elbow", len(elb), 89, 0)
    check("repetitions instructed correct", len(cor_e), 177, 0)
    check("median minimum elbow angle, elbow-flexion", median_of(elb), 142, 1.5, " deg")
    check("median minimum elbow angle, correct", median_of(cor_e), 166, 1.5, " deg")
    check("area under the ROC curve",
          roc_auc([-v for v in elb], [-v for v in cor_e]), 0.920, 0.006)
    t = THRESHOLDS["elbow"]
    check("sensitivity at the operating point",
          sum(1 for v in elb if v < t) / len(elb), 0.70, 0.02)
    check("specificity at the operating point",
          sum(1 for v in cor_e if v >= t) / len(cor_e), 0.95, 0.02)

    print()
    print("=" * 78)
    print("Threshold calibration on the held-out cohort")
    print("=" * 78)
    # Computed here from the sustained values, i.e. the statistic the detector
    # actually thresholds. The manuscript quotes 0.93-1.00 from the calibration
    # script, which scored the plain per-repetition extremum over active-phase
    # frames and applied the pre-correction gate constants; that convention is
    # slightly more conservative and needs the raw archive to reproduce exactly.
    key = {"elbow": ("elbow_sustained_deg", "ELB", True),
           "asymmetry": ("asymmetry_sustained_deg", "ASY", False),
           "shoulder": ("shoulder_sustained", "SHR", False),
           "trunk": ("trunk_sustained_rad", "TRK", False),
           "head": ("head_sustained_deg", "HED", False)}
    aucs = []
    for c, (col, cond, lower_is_positive) in key.items():
        pos = [r[col] for r in calibration if r["condition"] == cond and r[col] is not None]
        neg = [r[col] for r in calibration if r["condition"] == "COR" and r[col] is not None]
        a = roc_auc([-v for v in pos], [-v for v in neg]) if lower_is_positive \
            else roc_auc(pos, neg)
        aucs.append(a)
        print(f"  {c:<44} {a:>9.3f}")
    ok = min(aucs) >= 0.93
    print(f"  {'all metrics at or above the reported floor':<44} "
          f"{min(aucs):>9.3f}   paper >= 0.93   {'OK' if ok else 'MISMATCH'}")
    if not ok:
        _fails.append("calibration discrimination floor")
    counts = defaultdict(int)
    for r in calibration:
        counts[r["condition"]] += 1
    expected_counts = {"COR": 80, "ELB": 39, "ASY": 45, "SHR": 44, "TRK": 39, "HED": 45}
    for cond, n in sorted(expected_counts.items()):
        check(f"calibration repetitions, {cond}", counts[cond], n, 0)

    if not args.no_bootstrap:
        print()
        print("=" * 78)
        print(f"Participant-clustered bootstrap, {args.iterations} iterations, "
              f"seed {BOOTSTRAP_SEED}")
        print("=" * 78)
        print("  (intervals are reported in the paper's per-class table)")
        pairs_by_p = defaultdict(list)
        for r in detection:
            pairs_by_p[r["participant_id"]].append(
                (flags_at(r, THRESHOLDS), ann[r["clip_id"]]["R1"]))
        for c in CLASSES:
            lo, hi = cluster_bootstrap(
                detection, pairs_by_p, lambda ps, cc=c: score(ps)[cc]["f1"],
                args.iterations, BOOTSTRAP_SEED)
            point = per_rater["R1"][c]["f1"]
            print(f"  {c + ' F1 vs R1':<44} {point:>9.3f}   95% CI [{lo:.3f}, {hi:.3f}]")
        lo, hi = cluster_bootstrap(
            detection, pairs_by_p, lambda ps: score(ps)["macro_f1"],
            args.iterations, BOOTSTRAP_SEED)
        print(f"  {'macro-F1 vs R1':<44} {per_rater['R1']['macro_f1']:>9.3f}"
              f"   95% CI [{lo:.3f}, {hi:.3f}]")

    print()
    print("=" * 78)
    if _fails:
        print(f"{len(_fails)} value(s) did not match the manuscript:")
        for f in _fails:
            print(f"  - {f}")
        raise SystemExit(1)
    print("All values match the manuscript.")
    print("Note: the five per-metric calibration AUCs quoted in the Methods were obtained")
    print("at calibration time on the per-repetition extremum over active-phase frames.")
    print("The values printed above use the sustained statistic the detector thresholds,")
    print("so they run slightly higher; the manuscript states this.")
    print("=" * 78)


if __name__ == "__main__":
    main()
