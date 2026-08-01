#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Derive the published repetition-level tables from the raw keypoint archive.

This script documents the provenance of everything in `data/`. It requires the
raw per-frame keypoint recordings, which are NOT part of this release (see
README, "What is not included"); it is published so that the derivation is
auditable rather than asserted.

Input   : a directory holding `study.db` (session/repetition index) and
          `blocks/*.jsonl` (per-frame keypoints, one JSON object per frame).
Output  : data/repetitions.csv, data/annotations.csv, data/consensus.csv,
          data/participants.csv

Geometry follows the corrected (isotropic) convention: MediaPipe returns
x = px/W and y = px/H, so on a non-square frame the axes are scaled
differently and in-plane angles are not preserved. Every landmark therefore
gets x *= W/H before any geometry is computed, which is equivalent to working
in pixel coordinates up to a global scale (a global scale affects neither
angles nor length ratios).

Usage:
    python3 extract_repetition_tables.py --data /path/to/archive --out ../data
"""
import argparse
import csv
import json
import math
import os
import sqlite3

# ---------------------------------------------------------------- geometry --
# BlazePose landmark indices
NOSE, LEAR, REAR = 0, 7, 8
LSHO, RSHO, LELB, RELB, LWRI, RWRI, LHIP, RHIP = 11, 12, 13, 14, 15, 16, 23, 24
KEY_INFRAME = (NOSE, LSHO, RSHO, LHIP, RHIP)
CLASSES = ["elbow", "asymmetry", "shoulder", "trunk", "head"]

# Calibrated operating points (isotropic coordinates; Youden's index under a
# false-positive rate <= 10% on the nine-participant calibration cohort).
THRESHOLDS = {"elbow": 153.4, "asymmetry": 13.2, "shoulder": 0.174,
              "trunk": 0.090, "head": 6.4}
TRUNK_SUPPRESS = 0.072   # above this trunk tilt, asymmetry and head are not scored
VIOLATION_MS = 250.0     # a flag is raised once a violation accumulates this long
MAX_FRAME_GAP_MS = 100.0 # a dropped detection must not inflate the elapsed time


def deg(r):
    return r * 180.0 / math.pi


def angle_between(ux, uy, vx, vy):
    d = (ux * vx + uy * vy) / (math.hypot(ux, uy) * math.hypot(vx, vy) + 1e-9)
    return math.acos(min(1.0, max(-1.0, d)))


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def elbow_angle(lms, s, e, w):
    S, E, W = lms[s], lms[e], lms[w]
    return deg(angle_between(S[0] - E[0], S[1] - E[1], W[0] - E[0], W[1] - E[1]))


def elev_vert(lms, s, e):
    """Shoulder->elbow angle from frame vertical (0 deg = arm at the side).

    Measured against the frame rather than the trunk so that trunk lean does
    not leak into the inter-limb asymmetry metric.
    """
    S, E = lms[s], lms[e]
    return deg(angle_between(E[0] - S[0], E[1] - S[1], 0, 1))


def sho_mid_y(lms):
    return (lms[LSHO][1] + lms[RSHO][1]) / 2


def sho_width(lms):
    a, b = lms[LSHO], lms[RSHO]
    return math.hypot(a[0] - b[0], a[1] - b[1]) or 1e-6


def trunk_tilt(lms):
    sx = (lms[LSHO][0] + lms[RSHO][0]) / 2 - (lms[LHIP][0] + lms[RHIP][0]) / 2
    sy = sho_mid_y(lms) - (lms[LHIP][1] + lms[RHIP][1]) / 2
    return angle_between(sx, sy, 0, -1)


def ear_tilt(lms):
    """Tilt of the ear-to-ear line from horizontal (lateral head tilt)."""
    a, b = lms[LEAR], lms[REAR]
    return deg(angle_between(a[0] - b[0], a[1] - b[1], 1, 0))


# ------------------------------------------------------------------ loading --
def load_frames(path, aspect):
    """Read a per-frame keypoint file and apply the isotropic correction."""
    frames = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                fr = json.loads(line)
            except ValueError:
                continue
            if not isinstance(fr.get("landmarks"), list):
                continue
            fr["landmarks"] = [[lm[0] * aspect] + list(lm[1:]) for lm in fr["landmarks"]]
            frames.append(fr)
    return frames


def in_frame(lms, aspect):
    """All reference landmarks inside the image; outside it MediaPipe extrapolates.

    The bound is checked on the original normalisation, so x is divided back
    by the aspect ratio first.
    """
    for i in KEY_INFRAME:
        x, y = lms[i][0] / aspect, lms[i][1]
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return False
    return True


def baseline_from_rest(frames, aspect):
    """Functional zero taken over rest frames (arms down, landmarks in frame).

    More robust than the first seconds of the recording, which may catch the
    participant still settling into the chair.
    """
    rest = [fr for fr in frames
            if fr.get("phase") == "rest" and in_frame(fr["landmarks"], aspect)]
    if len(rest) < 5:
        rest = [fr for fr in frames if in_frame(fr["landmarks"], aspect)]
    if not rest:
        return None
    lms = [fr["landmarks"] for fr in rest]
    return {"shoY": median([sho_mid_y(m) for m in lms]),
            "shoW": median([sho_width(m) for m in lms]),
            "ear": median([ear_tilt(m) for m in lms])}


# ------------------------------------------------------------ per-repetition --
def repetition_series(frames, base, start_ms, end_ms, aspect):
    """Per-frame metric values inside one repetition, with elapsed time."""
    out, prev_t = [], None
    for fr in frames:
        t = fr.get("t")
        if t is None or t < start_ms or t > end_ms:
            continue
        lms = fr["landmarks"]
        if not in_frame(lms, aspect):
            prev_t = t
            continue
        dt = 0.0 if prev_t is None else min(t - prev_t, MAX_FRAME_GAP_MS)
        prev_t = t
        out.append({
            "dt": dt,
            "elbow": min(elbow_angle(lms, LSHO, LELB, LWRI),
                         elbow_angle(lms, RSHO, RELB, RWRI)),
            "asymmetry": abs(elev_vert(lms, LSHO, LELB) - elev_vert(lms, RSHO, RELB)),
            "shoulder": max(0.0, (base["shoY"] - sho_mid_y(lms)) / base["shoW"]),
            "trunk": trunk_tilt(lms),
            "head": abs(ear_tilt(lms) - base["ear"]),
        })
    return out


def sustained_high(series, key, gate=None):
    """Largest value v for which time spent at or above v reaches the window.

    This is the sufficient statistic for the 250 ms rule: the flag fires at a
    threshold T exactly when T <= v, so the published value reproduces the
    decision at *any* threshold without needing the per-frame series.
    """
    vals = sorted(((f[key], f["dt"]) for f in series if gate is None or gate(f)),
                  key=lambda p: -p[0])
    acc = 0.0
    for v, dt in vals:
        acc += dt
        if acc >= VIOLATION_MS:
            return v
    return None


def sustained_low(series, key, gate=None):
    """Mirror of `sustained_high` for a metric that fires when it falls below T."""
    vals = sorted(((f[key], f["dt"]) for f in series if gate is None or gate(f)),
                  key=lambda p: p[0])
    acc = 0.0
    for v, dt in vals:
        acc += dt
        if acc >= VIOLATION_MS:
            return v
    return None


def summarise(series):
    """Sustained values, plus plain extrema for description."""
    straight_upright = lambda f: (f["trunk"] < TRUNK_SUPPRESS
                                  and f["elbow"] >= THRESHOLDS["elbow"])
    upright = lambda f: f["trunk"] < TRUNK_SUPPRESS
    return {
        "elbow_sustained_deg": sustained_low(series, "elbow"),
        "asymmetry_sustained_deg": sustained_high(series, "asymmetry", straight_upright),
        "shoulder_sustained": sustained_high(series, "shoulder"),
        "trunk_sustained_rad": sustained_high(series, "trunk"),
        "head_sustained_deg": sustained_high(series, "head", upright),
        "elbow_min_deg": min(f["elbow"] for f in series),
        "asymmetry_max_deg": max(f["asymmetry"] for f in series),
        "shoulder_max": max(f["shoulder"] for f in series),
        "trunk_max_rad": max(f["trunk"] for f in series),
        "head_max_deg": max(f["head"] for f in series),
        "n_frames": len(series),
        "duration_ms": round(sum(f["dt"] for f in series)),
    }


def flags_from(summary, thresholds=THRESHOLDS):
    """Apply thresholds to the sustained values. Reproduces the published flags."""
    def high(v, t):
        return 0 if v is None else int(v >= t)
    return {
        "elbow": 0 if summary["elbow_sustained_deg"] is None
                 else int(summary["elbow_sustained_deg"] < thresholds["elbow"]),
        "asymmetry": high(summary["asymmetry_sustained_deg"], thresholds["asymmetry"]),
        "shoulder": high(summary["shoulder_sustained"], thresholds["shoulder"]),
        "trunk": high(summary["trunk_sustained_rad"], thresholds["trunk"]),
        "head": high(summary["head_sustained_deg"], thresholds["head"]),
    }


# -------------------------------------------------------------------- export --
COHORT = {"V": "calibration", "P": "detection"}
FIELDS = (["clip_id", "participant_id", "cohort", "condition", "repetition"]
          + [f"pred_{c}" for c in CLASSES]
          + ["elbow_sustained_deg", "asymmetry_sustained_deg", "shoulder_sustained",
             "trunk_sustained_rad", "head_sustained_deg",
             "elbow_min_deg", "asymmetry_max_deg", "shoulder_max", "trunk_max_rad",
             "head_max_deg", "n_frames", "duration_ms", "fps"])


def r6(v):
    return "" if v is None else f"{v:.6g}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="archive with study.db and blocks/")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "data"))
    ap.add_argument("--aspect", type=float, default=1280.0 / 720.0)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    db = sqlite3.connect(os.path.join(args.data, "study.db"))
    db.row_factory = sqlite3.Row
    blocks = db.execute(
        "SELECT id, uid, participant_id, condition, jsonl_path, fps FROM blocks "
        "WHERE trial = 0 AND voided_at IS NULL ORDER BY participant_id, condition"
    ).fetchall()

    reps_out, counts = [], {}
    for b in blocks:
        path = os.path.join(args.data, b["jsonl_path"])
        if not os.path.exists(path):
            continue
        frames = load_frames(path, args.aspect)
        base = baseline_from_rest(frames, args.aspect)
        if base is None:
            continue
        reps = db.execute(
            "SELECT rep_num, start_ms, end_ms FROM reps WHERE block_id = ? "
            "AND voided_at IS NULL ORDER BY rep_num", (b["id"],)).fetchall()
        for r in reps:
            series = repetition_series(frames, base, r["start_ms"], r["end_ms"], args.aspect)
            if not series:
                continue
            s = summarise(series)
            f = flags_from(s)
            cohort = COHORT.get(b["participant_id"][0], "other")
            counts[cohort] = counts.get(cohort, 0) + 1
            reps_out.append({
                "clip_id": f"{b['participant_id']}_{b['condition']}_rep{r['rep_num']}",
                "participant_id": b["participant_id"], "cohort": cohort,
                "condition": b["condition"], "repetition": r["rep_num"],
                **{f"pred_{c}": f[c] for c in CLASSES},
                **{k: (r6(v) if isinstance(v, float) else v) for k, v in s.items()},
                "fps": b["fps"],
            })

    reps_out.sort(key=lambda d: (d["participant_id"], d["condition"], d["repetition"]))
    with open(os.path.join(out, "repetitions.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(reps_out)
    print(f"repetitions.csv: {len(reps_out)} rows {counts}")

    # ---- annotations (detection cohort; both clinicians labelled every repetition)
    ann = db.execute("""
        SELECT b.participant_id || '_' || b.condition || '_rep' || r.rep_num AS clip_id,
               a.rater_id, a.elbow, a.asymmetry, a.shoulder, a.trunk, a.head, a.confidence
        FROM annotations a
        JOIN reps r ON r.id = a.rep_id
        JOIN blocks b ON b.id = r.block_id
        WHERE b.trial = 0 AND b.voided_at IS NULL AND r.voided_at IS NULL
          AND b.participant_id LIKE 'P%'
        ORDER BY clip_id, a.rater_id""").fetchall()
    with open(os.path.join(out, "annotations.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["clip_id", "rater"] + CLASSES + ["confidence"])
        for a in ann:
            w.writerow([a["clip_id"], a["rater_id"]] + [a[c] for c in CLASSES]
                       + [a["confidence"]])
    print(f"annotations.csv: {len(ann)} rows")

    # ---- consensus: repetitions the two clinicians agreed on, label by label
    by_clip = {}
    for a in ann:
        by_clip.setdefault(a["clip_id"], {})[a["rater_id"]] = a
    with open(os.path.join(out, "consensus.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["clip_id", "agreed"] + CLASSES)
        n_agreed = 0
        for cid in sorted(by_clip):
            rs = by_clip[cid]
            if len(rs) < 2:
                continue
            r1, r2 = rs.get("R1"), rs.get("R2")
            agreed = int(all(r1[c] == r2[c] for c in CLASSES))
            n_agreed += agreed
            w.writerow([cid, agreed] + [r1[c] if agreed else "" for c in CLASSES])
    print(f"consensus.csv: {n_agreed} fully agreed repetitions")

    # ---- participants: cohort membership and repetition counts only
    with open(os.path.join(out, "participants.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["participant_id", "cohort", "conditions", "repetitions"])
        agg = {}
        for d in reps_out:
            k = (d["participant_id"], d["cohort"])
            e = agg.setdefault(k, {"conds": set(), "n": 0})
            e["conds"].add(d["condition"])
            e["n"] += 1
        for (pid, cohort), e in sorted(agg.items()):
            w.writerow([pid, cohort, len(e["conds"]), e["n"]])
        print(f"participants.csv: {len(agg)} participants")

    db.close()


if __name__ == "__main__":
    main()
