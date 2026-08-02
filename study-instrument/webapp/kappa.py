#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Межэкспертное согласие (Cohen's kappa) двух врачей-разметчиков по каждой из
5 меток компенсаций, на клипах, которые разметили ОБА (пересечение).

Только стандартная библиотека — запускать host-питоном на VM, не в контейнере:
    cd ~/study-webapp && python3 kappa.py            # когорта P по умолчанию
    python3 kappa.py --cohort V --raters R1,R2
    python3 kappa.py --show-disagreements            # + список расхождений

Это НАДЁЖНОСТЬ ЭТАЛОНА (врач против врача), а НЕ результат системы. Финальные
числа статьи считаются на полном наборе с bootstrap-CI по участникам
(см. analysis.py); здесь — быстрый предварительный пересчёт по мере роста
пересечения. На малом N доверительные интервалы широкие.
"""

import argparse
import os
import sqlite3
import sys

METRICS = ["elbow", "asymmetry", "shoulder", "trunk", "head"]
RU = {"elbow": "Локоть", "asymmetry": "Асимметрия", "shoulder": "Плечо",
      "trunk": "Корпус", "head": "Голова"}

# «Активный» клип: зачётный блок, не исключён ни блок, ни повтор (как в server.py)
ACTIVE_CLIP = "b.trial = 0 AND b.voided_at IS NULL AND r.voided_at IS NULL"


def landis_koch(k):
    """Словесная оценка κ по шкале Landis & Koch (1977)."""
    if k is None:
        return "—"
    if k < 0:
        return "хуже случайного"
    if k < 0.20:
        return "ничтожное"
    if k < 0.40:
        return "слабое"
    if k < 0.60:
        return "умеренное"
    if k < 0.80:
        return "существенное"
    return "почти идеальное"


def cohen_kappa(pairs):
    """Cohen's kappa для бинарных меток.
    Возвращает (kappa|None, p_observed, p1_rate, p2_rate). kappa=None, если оба
    эксперта всегда ставят один и тот же класс (p_e=1 → деление на ноль)."""
    n = len(pairs)
    if n == 0:
        return None, 0.0, 0.0, 0.0
    agree = sum(1 for a, b in pairs if a == b)
    po = agree / n
    p1 = sum(a for a, _ in pairs) / n      # доля «да» у 1-го врача
    p2 = sum(b for _, b in pairs) / n      # доля «да» у 2-го
    pe = p1 * p2 + (1 - p1) * (1 - p2)     # ожидаемое случайное согласие
    kappa = None if abs(1 - pe) < 1e-12 else (po - pe) / (1 - pe)
    return kappa, po, p1, p2


def fetch_overlap(db, cohort, r1, r2):
    """Метки обоих врачей на активных клипах когорты, размеченных ОБОИМИ."""
    db.row_factory = sqlite3.Row
    like = cohort + "%"
    cols = ", ".join("a1.%s AS r1_%s" % (m, m) for m in METRICS) + ", " + \
           ", ".join("a2.%s AS r2_%s" % (m, m) for m in METRICS)
    return db.execute(
        f"""SELECT b.participant_id AS pid, r.clip_uid AS clip, {cols}
            FROM reps r
            JOIN blocks b ON b.id = r.block_id
            JOIN annotations a1 ON a1.rep_id = r.id AND a1.rater_id = ?
            JOIN annotations a2 ON a2.rep_id = r.id AND a2.rater_id = ?
            WHERE {ACTIVE_CLIP} AND b.participant_id LIKE ?
            ORDER BY b.participant_id, r.clip_uid""",
        (r1, r2, like),
    ).fetchall()


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Cohen's kappa между двумя разметчиками по 5 меткам")
    ap.add_argument("--db", default=os.path.join(here, "data", "study.db"),
                    help="путь к study.db (по умолчанию ./data/study.db рядом со скриптом)")
    ap.add_argument("--cohort", default="P", help="префикс когорты (по умолчанию P)")
    ap.add_argument("--raters", default="R1,R2", help="два rater_id через запятую (по умолчанию R1,R2)")
    ap.add_argument("--show-disagreements", action="store_true",
                    help="вывести клипы, где врачи разошлись, по каждой метрике")
    args = ap.parse_args()

    raters = [x.strip() for x in args.raters.split(",") if x.strip()]
    if len(raters) != 2:
        sys.exit("--raters: нужно ровно два id через запятую, напр. R1,R2")
    r1, r2 = raters
    if not os.path.isfile(args.db):
        sys.exit("Не найден БД: %s" % args.db)

    db = sqlite3.connect(args.db, timeout=30)
    rows = fetch_overlap(db, args.cohort, r1, r2)
    n = len(rows)

    print("Межэкспертное согласие (Cohen's κ): %s vs %s, когорта %s" % (r1, r2, args.cohort))
    print("Пересечение (разметили ОБА, активные клипы): N = %d" % n)
    if n == 0:
        print("\nПока нет клипов, размеченных обоими — каппу считать не на чем.")
        return
    print()

    header = "%-12s %7s  %6s  %-13s  %-26s %s" % (
        "Метрика", "κ", "согл.", "«да» R1/R2", "2×2 [оба+, R1+R2-, R1-R2+, оба-]", "оценка")
    print(header)
    print("-" * len(header))

    kappas, disagreements = [], {}
    for m in METRICS:
        pairs = [(int(r["r1_%s" % m]), int(r["r2_%s" % m])) for r in rows]
        k, po, p1, p2 = cohen_kappa(pairs)
        kappas.append(k)
        bp = sum(1 for a, b in pairs if a and b)
        r1o = sum(1 for a, b in pairs if a and not b)
        r2o = sum(1 for a, b in pairs if b and not a)
        bn = sum(1 for a, b in pairs if not a and not b)
        disagreements[m] = [r for r in rows if int(r["r1_%s" % m]) != int(r["r2_%s" % m])]
        print("%-12s %7s  %5.0f%%  %3.0f%% / %-3.0f%%  [%2d, %2d, %2d, %2d]%s  %s" % (
            RU[m], ("n/a" if k is None else "%+.2f" % k), 100 * po,
            100 * p1, 100 * p2, bp, r1o, r2o, bn, "", landis_koch(k)))

    valid = [k for k in kappas if k is not None]
    if valid:
        avg = sum(valid) / len(valid)
        print("-" * len(header))
        print("%-12s %7s%s%s" % ("среднее", "%+.2f" % avg, " " * 26, landis_koch(avg)))

    if args.show_disagreements:
        print("\nРасхождения (метка: участник/клип — R1 vs R2):")
        any_dis = False
        for m in METRICS:
            for r in disagreements[m]:
                any_dis = True
                print("  %-11s %s/%s  R1=%d R2=%d" % (
                    RU[m], r["pid"], r["clip"], int(r["r1_%s" % m]), int(r["r2_%s" % m])))
        if not any_dis:
            print("  — нет, полное совпадение по всем меткам")

    print("\nПримечание: это надёжность эталона (врач×врач), не результат системы.")
    print("На малом N интервалы широкие; финал — на полном наборе с bootstrap-CI")
    print("по участникам (analysis.py). Шкала: Landis & Koch (1977).")


if __name__ == "__main__":
    main()
