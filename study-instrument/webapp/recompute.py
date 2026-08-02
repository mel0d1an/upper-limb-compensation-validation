#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Канонический оффлайн-пересчёт предсказаний системы из СЫРЫХ ключевых точек.

Зачем: живые флаги (в record.html) считаются на лету и зависят от качества
калибровки в момент записи (первые ~1.5 с). У готовых видео старт бывает «грязный»
(человек ещё садится, голова/плечи вне кадра) → битый «функциональный ноль» →
метрики плеча и головы ломаются. Этот скрипт берёт «ноль» устойчиво — по кадрам
ПОКОЯ (руки опущены, точки в кадре) — и пересчитывает все метрики и флаги по
определениям v1.1. Только stdlib; вход — data/ (study.db + blocks/*.jsonl).

Запуск:  python3 recompute.py [--data DIR] [--out predictions_v1.1.csv]
"""

import argparse
import csv
import json
import math
import os
import sqlite3
import sys

# --- пороги v1.1 (черновые: подобраны по клиническому референсу, финал — по пилоту) ---
VERSION = "v1.1-recompute-20260613"
T_ELBOW_DEG = 135      # локоть согнут, если minугол < 135° (прямая рука ~155–170°)
T_ASYM_DEG = 20        # асимметрия рук (к вертикали) ≥ 20°
T_SHOULDER = 0.25      # подъём плеч / ширина плеч, ноль по покою; 0.25 — порог-«пол»,
                       # где COR ещё 0 ложных, а чувствительность SHR 74%→82% (калибровка на 6 чел.)
T_TRUNK_RAD = 0.05     # наклон корпуса ≥ 0.05 рад (~2.9°)
T_HEAD_DEG = 15        # наклон головы: наклон линии ушей ≥ 15°
T_TRUNK_SUPPRESS = 0.04  # при наклоне корпуса ≥ этого асимметрию и голову не считаем
T_VIOLATION_MS = 250   # флаг ставится, если нарушение суммарно ≥ 250 мс в повторении

# индексы BlazePose
NOSE, LEAR, REAR = 0, 7, 8
LSHO, RSHO, LELB, RELB, LWRI, RWRI, LHIP, RHIP = 11, 12, 13, 14, 15, 16, 23, 24
KEY_INFRAME = (NOSE, LSHO, RSHO, LHIP, RHIP)
CLASSES = ["elbow", "asymmetry", "shoulder", "trunk", "head"]


def deg(r):
    return r * 180 / math.pi


def angle_between(ux, uy, vx, vy):
    d = (ux * vx + uy * vy) / (math.hypot(ux, uy) * math.hypot(vx, vy) + 1e-9)
    return math.acos(min(1, max(-1, d)))


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0.0


def in_frame(lms):
    """Все опорные точки внутри кадра [0,1] — иначе MediaPipe экстраполирует мусор."""
    for i in KEY_INFRAME:
        x, y = lms[i][0], lms[i][1]
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return False
    return True


def elbow_angle(lms, s, e, w):
    S, E, W = lms[s], lms[e], lms[w]
    return deg(angle_between(S[0] - E[0], S[1] - E[1], W[0] - E[0], W[1] - E[1]))


def elev_vert(lms, s, e):
    """Угол плеча→локоть к вертикали кадра (0° — рука опущена). Не зависит от
    наклона корпуса, поэтому асимметрия не подмешивается из наклона."""
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
    """Наклон линии ушей к горизонтали (наклон головы вбок)."""
    a, b = lms[LEAR], lms[REAR]
    return deg(angle_between(a[0] - b[0], a[1] - b[1], 1, 0))


def load_frames(path):
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
            if isinstance(fr.get("landmarks"), list):
                frames.append(fr)
    return frames


def baseline_from_rest(frames):
    """«Функциональный ноль» по кадрам покоя (руки опущены, в кадре). Надёжнее,
    чем первые 1.5 с: устойчив к грязному старту видео."""
    rest = [fr for fr in frames
            if fr.get("phase") == "rest" and in_frame(fr["landmarks"])]
    if len(rest) < 5:  # запасной вариант: любые кадры в кадре
        rest = [fr for fr in frames if in_frame(fr["landmarks"])]
    if not rest:
        return None
    lms = [fr["landmarks"] for fr in rest]
    return {
        "shoY": median([sho_mid_y(m) for m in lms]),
        "shoW": median([sho_width(m) for m in lms]),
        "ear": median([ear_tilt(m) for m in lms]),
    }


def rep_flags(frames, base, start_ms, end_ms):
    """Накопить время нарушений по кадрам повторения [start_ms, end_ms] и
    вернуть бинарные флаги (правило 250 мс)."""
    acc = {c: 0.0 for c in CLASSES}
    prev_t = None
    for fr in frames:
        t = fr.get("t")
        if t is None or t < start_ms or t > end_ms:
            continue
        lms = fr["landmarks"]
        if not in_frame(lms):
            prev_t = t
            continue
        dt = 0.0 if prev_t is None else min(t - prev_t, 100)
        prev_t = t
        elb = min(elbow_angle(lms, LSHO, LELB, LWRI), elbow_angle(lms, RSHO, RELB, RWRI))
        asym = abs(elev_vert(lms, LSHO, LELB) - elev_vert(lms, RSHO, RELB))
        sh = max(0.0, (base["shoY"] - sho_mid_y(lms)) / base["shoW"])
        tr = trunk_tilt(lms)
        hd = abs(ear_tilt(lms) - base["ear"])
        straight = elb >= T_ELBOW_DEG
        upright = tr < T_TRUNK_SUPPRESS
        if elb < T_ELBOW_DEG:
            acc["elbow"] += dt
        if asym >= T_ASYM_DEG and upright and straight:
            acc["asymmetry"] += dt
        if sh >= T_SHOULDER:
            acc["shoulder"] += dt
        if tr >= T_TRUNK_RAD:
            acc["trunk"] += dt
        if hd >= T_HEAD_DEG and upright:
            acc["head"] += dt
    return {c: (1 if acc[c] >= T_VIOLATION_MS else 0) for c in CLASSES}


def main():
    ap = argparse.ArgumentParser(description="Канонический пересчёт предсказаний из keypoints (v1.1)")
    ap.add_argument("--data", default=os.path.join(os.path.dirname(__file__), "data"),
                    help="каталог данных (study.db + blocks/)")
    ap.add_argument("--out", default="predictions_v1.1.csv", help="выходной CSV")
    ap.add_argument("--prefix", default="", help="только участники с кодом на этот префикс "
                    "(напр. V — валидация, P — пилот); пусто = все")
    args = ap.parse_args()

    db_path = os.path.join(args.data, "study.db")
    blocks_dir = os.path.join(args.data, "blocks")
    if not os.path.exists(db_path):
        sys.exit(f"нет study.db в {args.data}")

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    where = "trial = 0 AND voided_at IS NULL"
    params = ()
    if args.prefix:
        where += " AND participant_id LIKE ?"
        params = (args.prefix + "%",)
    blocks = db.execute(
        f"""SELECT id, uid, participant_id, condition, trial, voided_at, jsonl_path, fps
            FROM blocks WHERE {where} ORDER BY participant_id, condition""", params
    ).fetchall()

    rows = []
    # сводка по условиям для контроля: сколько повторений с каждым флагом
    by_cond = {}
    skipped = []
    for b in blocks:
        jsonl = os.path.join(args.data, b["jsonl_path"])
        if not os.path.exists(jsonl):
            skipped.append(f"{b['participant_id']}/{b['condition']}: нет {b['jsonl_path']}")
            continue
        frames = load_frames(jsonl)
        base = baseline_from_rest(frames)
        if base is None:
            skipped.append(f"{b['participant_id']}/{b['condition']}: нет валидных кадров для нуля")
            continue
        reps = db.execute(
            """SELECT rep_num, start_ms, end_ms FROM reps
               WHERE block_id=? AND voided_at IS NULL ORDER BY rep_num""",
            (b["id"],),
        ).fetchall()
        agg = by_cond.setdefault(b["condition"], {c: 0 for c in CLASSES} | {"n": 0})
        for r in reps:
            flags = rep_flags(frames, base, r["start_ms"], r["end_ms"])
            rows.append((
                f"{b['participant_id']}_{b['condition']}_rep{r['rep_num']}",
                b["participant_id"], b["condition"], r["rep_num"],
                flags["elbow"], flags["asymmetry"], flags["shoulder"],
                flags["trunk"], flags["head"],
                VERSION, b["fps"], f"{b['uid']}.jsonl", r["start_ms"], r["end_ms"],
            ))
            for c in CLASSES:
                agg[c] += flags[c]
            agg["n"] += 1

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        f.write("﻿")
        w = csv.writer(f)
        w.writerow(["clip_id", "participant_id", "condition", "rep",
                    "elbow", "asymmetry", "shoulder", "trunk", "head",
                    "threshold_set", "fps", "keypoints_file", "rep_start_ms", "rep_end_ms"])
        w.writerows(rows)

    # контрольная матрица
    print(f"Пересчитано {len(rows)} повторений → {args.out}  (пороги {VERSION})")
    if skipped:
        print("Пропущено:")
        for s in skipped:
            print("  ", s)
    print("\nФлаги по условиям (доля повторений):")
    print(f"  {'усл':4} {'n':>3}  " + "  ".join(f"{c[:4]:>5}" for c in CLASSES))
    for cond in sorted(by_cond):
        a = by_cond[cond]
        print(f"  {cond:4} {a['n']:>3}  " + "  ".join(f"{a[c]}/{a['n']:<3}" for c in CLASSES))


if __name__ == "__main__":
    main()
