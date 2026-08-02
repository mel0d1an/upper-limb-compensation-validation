#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Анализ валидации детекции компенсаций против разметки клиницистов.

Только стандартная библиотека Python 3.9+. Команды:

  kappa r1.csv r2.csv          каппа Коэна между рейтерами (per-class)
  consensus r1.csv r2.csv -o consensus.csv
                               консенсусная разметка; разногласия помечаются
                               adjudication_needed=1 (заполнить вручную)
  evaluate consensus.csv predictions.csv
                               P/R/F1 per-class + 95% ДИ (бутстреп по
                               участникам), accuracy, каппа система-консенсус,
                               матрица «условие × частота срабатываний»
  sensitivity consensus.csv "predictions_thr*.csv"
                               сравнение F1 по вариантам порогов

Форматы файлов — см. templates/ и README.md.
"""
import argparse
import csv
import glob
import random
import sys
from collections import defaultdict

CLASSES = ["elbow", "asymmetry", "shoulder", "trunk", "head"]
N_BOOT = 10_000
SEED = 20260910  # дата дедлайна SI — фиксированный seed для воспроизводимости


def read_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit(f"Файл пуст: {path}")
    return rows


def need_int(row, col, path):
    v = (row.get(col) or "").strip()
    if v not in ("0", "1"):
        sys.exit(
            f"{path}: clip '{row.get('clip_id')}', колонка '{col}' = '{v}' "
            f"(ожидается 0 или 1). Разметка не завершена?"
        )
    return int(v)


def by_clip(rows, path):
    d = {}
    for r in rows:
        cid = (r.get("clip_id") or "").strip()
        if not cid:
            sys.exit(f"{path}: строка без clip_id")
        if cid in d:
            sys.exit(f"{path}: дубль clip_id '{cid}'")
        d[cid] = r
    return d


def cohen_kappa(a, b):
    """Каппа Коэна для двух бинарных векторов."""
    assert len(a) == len(b) and a
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    p1a = sum(a) / n
    p1b = sum(b) / n
    pe = p1a * p1b + (1 - p1a) * (1 - p1b)
    if pe == 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return p, r, f


# --------------------------------------------------------------- kappa ----
def cmd_kappa(args):
    r1 = by_clip(read_rows(args.rater1), args.rater1)
    r2 = by_clip(read_rows(args.rater2), args.rater2)
    common = sorted(set(r1) & set(r2))
    if set(r1) != set(r2):
        print(f"ВНИМАНИЕ: множества clip_id различаются; "
              f"общих {len(common)}, только в r1: {len(set(r1)-set(r2))}, "
              f"только в r2: {len(set(r2)-set(r1))}\n")
    print(f"Повторений в сравнении: {len(common)}\n")
    print(f"{'Класс':<12}{'Каппа':>8}{'Согласие %':>12}")
    kappas = []
    for c in CLASSES:
        a = [need_int(r1[k], c, args.rater1) for k in common]
        b = [need_int(r2[k], c, args.rater2) for k in common]
        k = cohen_kappa(a, b)
        agree = 100 * sum(1 for x, y in zip(a, b) if x == y) / len(a)
        kappas.append(k)
        print(f"{c:<12}{k:>8.3f}{agree:>11.1f}%")
    print(f"{'среднее':<12}{sum(kappas)/len(kappas):>8.3f}")
    full = sum(
        1 for k in common
        if all(r1[k][c].strip() == r2[k][c].strip() for c in CLASSES)
    )
    print(f"\nПолное совпадение всех 5 меток: {full}/{len(common)} "
          f"({100*full/len(common):.1f}%) — остальные пойдут на адъюдикацию.")


# ----------------------------------------------------------- consensus ----
def cmd_consensus(args):
    r1 = by_clip(read_rows(args.rater1), args.rater1)
    r2 = by_clip(read_rows(args.rater2), args.rater2)
    common = sorted(set(r1) & set(r2))
    out_cols = (["clip_id"] + CLASSES + ["adjudication_needed"]
                + [f"{c}_r1" for c in CLASSES] + [f"{c}_r2" for c in CLASSES])
    n_adj = 0
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_cols)
        w.writeheader()
        for k in common:
            row = {"clip_id": k}
            adj = False
            for c in CLASSES:
                a = need_int(r1[k], c, args.rater1)
                b = need_int(r2[k], c, args.rater2)
                row[f"{c}_r1"], row[f"{c}_r2"] = a, b
                if a == b:
                    row[c] = a
                else:
                    row[c] = ""  # заполняется адъюдикацией
                    adj = True
            row["adjudication_needed"] = int(adj)
            n_adj += adj
            w.writerow(row)
    print(f"Записано {len(common)} строк в {args.output}; "
          f"на адъюдикацию: {n_adj} ({100*n_adj/len(common):.1f}%).")
    if n_adj:
        print("Заполните пустые ячейки классов (решение третьего рейтера или "
              "консенсус) перед запуском evaluate.")


# ------------------------------------------------------------ evaluate ----
def load_joined(consensus_path, predictions_path):
    cons = by_clip(read_rows(consensus_path), consensus_path)
    preds = by_clip(read_rows(predictions_path), predictions_path)
    common = sorted(set(cons) & set(preds))
    missing = set(cons) - set(preds)
    if missing:
        print(f"ВНИМАНИЕ: {len(missing)} clip_id из консенсуса нет в "
              f"предсказаниях (пример: {sorted(missing)[:3]})\n")
    data = []
    for k in common:
        item = {
            "clip_id": k,
            "participant": (preds[k].get("participant_id") or "?").strip(),
            "condition": (preds[k].get("condition") or "?").strip(),
            "y": {c: need_int(cons[k], c, consensus_path) for c in CLASSES},
            "p": {c: need_int(preds[k], c, predictions_path) for c in CLASSES},
        }
        data.append(item)
    return data


def per_class_f1(data):
    out = {}
    for c in CLASSES:
        tp = sum(1 for d in data if d["y"][c] and d["p"][c])
        fp = sum(1 for d in data if not d["y"][c] and d["p"][c])
        fn = sum(1 for d in data if d["y"][c] and not d["p"][c])
        out[c] = prf(tp, fp, fn)
    return out


def cmd_evaluate(args):
    data = load_joined(args.consensus, args.predictions)
    n = len(data)
    print(f"Повторений в анализе: {n}; участников: "
          f"{len({d['participant'] for d in data})}\n")

    # точечные оценки
    print(f"{'Класс':<12}{'Prec':>7}{'Rec':>7}{'F1':>7}{'Kappa':>8}"
          f"{'Acc %':>8}")
    f1s = []
    for c in CLASSES:
        p, r, f = per_class_f1(data)[c]
        y = [d["y"][c] for d in data]
        pr = [d["p"][c] for d in data]
        k = cohen_kappa(y, pr)
        acc = 100 * sum(1 for a, b in zip(y, pr) if a == b) / n
        f1s.append(f)
        print(f"{c:<12}{p:>7.3f}{r:>7.3f}{f:>7.3f}{k:>8.3f}{acc:>7.1f}%")
    exact = sum(1 for d in data
                if all(d["y"][c] == d["p"][c] for c in CLASSES))
    print(f"{'macro':<12}{'':>7}{'':>7}{sum(f1s)/len(f1s):>7.3f}")
    print(f"\nExact match (все 5 меток верны): {exact}/{n} "
          f"({100*exact/n:.1f}%)")

    # кластерный бутстреп по участникам
    groups = defaultdict(list)
    for d in data:
        groups[d["participant"]].append(d)
    pids = sorted(groups)
    rng = random.Random(SEED)
    boots = {c: [] for c in CLASSES}
    boots["macro"] = []
    for _ in range(N_BOOT):
        sample = []
        for _ in pids:
            sample.extend(groups[rng.choice(pids)])
        fs = per_class_f1(sample)
        vals = [fs[c][2] for c in CLASSES]
        for c, v in zip(CLASSES, vals):
            boots[c].append(v)
        boots["macro"].append(sum(vals) / len(vals))
    print(f"\n95% ДИ для F1 (бутстреп по участникам, {N_BOOT} итераций):")
    for c in CLASSES + ["macro"]:
        b = sorted(boots[c])
        lo = b[int(0.025 * N_BOOT)]
        hi = b[int(0.975 * N_BOOT) - 1]
        print(f"  {c:<10} [{lo:.3f}; {hi:.3f}]")

    # матрица: сценарное условие × частота срабатывания флагов
    conds = sorted({d["condition"] for d in data})
    print("\nЧастота срабатывания флагов системы по сценарным условиям "
          "(доля повторений с флагом=1):")
    print(f"{'Условие':<10}" + "".join(f"{c:>11}" for c in CLASSES)
          + f"{'n':>6}")
    for cond in conds:
        sub = [d for d in data if d["condition"] == cond]
        cells = "".join(
            f"{sum(d['p'][c] for d in sub)/len(sub):>11.2f}" for c in CLASSES
        )
        print(f"{cond:<10}{cells}{len(sub):>6}")
    print("\n(та же матрица по консенсусной разметке — manipulation check):")
    print(f"{'Условие':<10}" + "".join(f"{c:>11}" for c in CLASSES)
          + f"{'n':>6}")
    for cond in conds:
        sub = [d for d in data if d["condition"] == cond]
        cells = "".join(
            f"{sum(d['y'][c] for d in sub)/len(sub):>11.2f}" for c in CLASSES
        )
        print(f"{cond:<10}{cells}{len(sub):>6}")


# --------------------------------------------------------- sensitivity ----
def cmd_sensitivity(args):
    files = sorted(glob.glob(args.pattern))
    if not files:
        sys.exit(f"Нет файлов по маске: {args.pattern}")
    print(f"{'Файл порогов':<40}" + "".join(f"{c:>11}" for c in CLASSES)
          + f"{'macro':>9}")
    for path in files:
        data = load_joined(args.consensus, path)
        fs = per_class_f1(data)
        vals = [fs[c][2] for c in CLASSES]
        cells = "".join(f"{v:>11.3f}" for v in vals)
        print(f"{path:<40}{cells}{sum(vals)/len(vals):>9.3f}")


# ---------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("kappa", help="каппа Коэна между двумя рейтерами")
    k.add_argument("rater1")
    k.add_argument("rater2")
    k.set_defaults(func=cmd_kappa)

    c = sub.add_parser("consensus", help="построить консенсусную разметку")
    c.add_argument("rater1")
    c.add_argument("rater2")
    c.add_argument("-o", "--output", default="consensus.csv")
    c.set_defaults(func=cmd_consensus)

    e = sub.add_parser("evaluate", help="оценка системы против консенсуса")
    e.add_argument("consensus")
    e.add_argument("predictions")
    e.set_defaults(func=cmd_evaluate)

    s = sub.add_parser("sensitivity", help="F1 по вариантам порогов")
    s.add_argument("consensus")
    s.add_argument("pattern", help='маска файлов, например "predictions_thr*.csv"')
    s.set_defaults(func=cmd_sensitivity)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
