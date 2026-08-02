#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сервер исследования: запись блоков добровольцев (исследователь)
и слепая разметка видео врачами.

Один файл, зависимости: Flask (+ waitress для прода).
Отладочный запуск: python3 server.py -> http://127.0.0.1:8742
"""

import contextlib
import csv
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from functools import wraps

from flask import (Flask, Response, g, jsonify, redirect, request, send_file,
                   send_from_directory)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATIC_DIR = os.path.join(BASE_DIR, "static")

CONDITIONS = ("COR", "ELB", "ASY", "SHR", "TRK", "HED")
FLAG_KEYS = ("elbow", "asymmetry", "shoulder", "trunk", "head")

# Допустимое условие блока: одно из 6 сценарных (режим «здоровый доброволец»)
# либо NAT<номер> для режима «пациент» (естественная проба, без сценария).
# NAT-номер делает clip_id уникальным при нескольких пробах одного пациента.
_NAT_RE = re.compile(r"NAT\d+")


def _valid_condition(c):
    return c in CONDITIONS or bool(_NAT_RE.fullmatch(c))

# «Активный» блок для всех путей чтения (очередь врача, прогресс, экспорты):
# зачётный (не пробный) и не исключённый. Псевдоним таблицы blocks — b.
# Исключение блока должно невидимо убирать его отовсюду, кроме списка блоков
# исследователя (там он показывается с пометкой и причиной).
ACTIVE_BLOCK = "b.trial = 0 AND b.voided_at IS NULL"
# «Активный клип» для путей чтения, где есть и блок (b), и повтор (r): кроме
# исключённого блока отсекаем и отдельно исключённые повторы (бракованные клипы).
ACTIVE_CLIP = ACTIVE_BLOCK + " AND r.voided_at IS NULL"

# ---------------------------------------------------------------- конфигурация

if not os.path.exists(CONFIG_PATH):
    raise SystemExit(
        "Не найден config.json рядом с server.py. "
        "Скопируйте config.example.json в config.json и заполните токены."
    )

with open(CONFIG_PATH, encoding="utf-8") as _f:
    CONFIG = json.load(_f)

RESEARCHER_TOKEN = CONFIG["researcher_token"]
RATERS = CONFIG.get("raters", [])
RATERS_BY_ID = {r["id"]: r for r in RATERS}

# Часовой пояс врачей (для ачивок «ночная смена»/«жаворонок» и счёта по дням):
# updated_at пишется в UTC, для «времени суток» сдвигаем на этот офсет. По
# умолчанию Москва (UTC+3).
TZ_OFFSET = timedelta(hours=float(CONFIG.get("tz_offset_hours", 3)))


def rater_display(rid):
    """Имя врача для табло/ачивок: display_name из конфига, иначе name, иначе id."""
    r = RATERS_BY_ID.get(rid) or {}
    return r.get("display_name") or r.get("name") or rid


def rater_gender(rid):
    """Род для согласования текста ачивок: 'f' (жен.) или 'm' (муж., по умолчанию)."""
    return ((RATERS_BY_ID.get(rid) or {}).get("gender") or "m")


def rival_of(rid):
    """Соперник в гонке разметки — другой врач, если их ровно двое (иначе None)."""
    if len(RATERS) != 2:
        return None
    for r in RATERS:
        if r["id"] != rid:
            return r
    return None

# Когорты: каждая — {name, prefix}. Врач при разметке выбирает когорту, и его
# очередь/прогресс ограничиваются участниками, чей код начинается с её префикса
# (напр. V — валидационная выборка, P — пилот/эксперимент). Исследователь так же
# экспортирует по когорте. Если список пуст — фильтрации нет (все участники).
COHORTS = CONFIG.get("cohorts", [])
ALLOWED_PREFIXES = set()
for _c in COHORTS:
    _p = str(_c.get("prefix", "")).strip()
    # без '_' и '%' — это wildcard'ы LIKE; префикс подставляется в LIKE-шаблон
    if not re.fullmatch(r"[A-Za-z0-9]+", _p):
        raise SystemExit("cohorts: prefix только буквы и цифры")
    _c["prefix"] = _p
    ALLOWED_PREFIXES.add(_p)

# Проверка токенов при старте: запрещаем короткие и шаблонные значения
for _label, _tok in [("researcher_token", RESEARCHER_TOKEN)] + [
    (f"raters[{_r['id']}].token", _r["token"]) for _r in RATERS
]:
    if not isinstance(_tok, str) or len(_tok) < 16 or "CHANGE-ME" in _tok:
        raise SystemExit(
            f"Небезопасный токен в config.json ({_label}): "
            "токен должен быть длиной не менее 16 символов и не содержать 'CHANGE-ME'. "
            "Сгенерируйте, например: python3 -c \"import secrets; print(secrets.token_urlsafe(24))\""
        )

DATA_DIR = os.path.join(BASE_DIR, CONFIG.get("data_dir", "data"))
BLOCKS_DIR = os.path.join(DATA_DIR, "blocks")
DB_PATH = os.path.join(DATA_DIR, "study.db")

os.makedirs(BLOCKS_DIR, exist_ok=True)

app = Flask(__name__)
# Лимит размера загрузки (видео + keypoints), из конфига
app.config["MAX_CONTENT_LENGTH"] = int(CONFIG.get("max_upload_mb", 300)) * 1024 * 1024

# ---------------------------------------------------------------- база данных

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  uid TEXT UNIQUE NOT NULL,
  client_uid TEXT UNIQUE,
  participant_id TEXT NOT NULL,
  condition TEXT NOT NULL,
  trial INTEGER NOT NULL DEFAULT 0,
  threshold_set TEXT NOT NULL,
  fps REAL NOT NULL DEFAULT 30,
  video_path TEXT NOT NULL,
  jsonl_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reps(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  block_id INTEGER NOT NULL REFERENCES blocks(id),
  rep_num INTEGER NOT NULL,
  start_ms INTEGER NOT NULL,
  end_ms INTEGER NOT NULL,
  elbow INTEGER NOT NULL, asymmetry INTEGER NOT NULL, shoulder INTEGER NOT NULL,
  trunk INTEGER NOT NULL, head INTEGER NOT NULL,
  clip_uid TEXT UNIQUE NOT NULL,
  UNIQUE(block_id, rep_num)
);
CREATE TABLE IF NOT EXISTS annotations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rater_id TEXT NOT NULL,
  rep_id INTEGER NOT NULL REFERENCES reps(id),
  elbow INTEGER NOT NULL, asymmetry INTEGER NOT NULL, shoulder INTEGER NOT NULL,
  trunk INTEGER NOT NULL, head INTEGER NOT NULL,
  confidence INTEGER NOT NULL,
  comments TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  created_at TEXT,                 -- момент ПЕРВОГО сохранения (не меняется при правке)
  UNIQUE(rater_id, rep_id)
);
-- Журнал аудита: append-only, фиксирует исключение/восстановление блоков и
-- безвозвратное стирание данных участника (отзыв согласия). Содержимое (видео,
-- метки) сюда не пишется — только факт действия, причина и счётчики.
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,            -- 'void' | 'restore' | 'erase'
  participant_id TEXT,
  block_uid TEXT,
  reason TEXT NOT NULL DEFAULT '',
  detail TEXT NOT NULL DEFAULT ''
);
-- Геймификация разметки (мотивация врачей): однократно заработанные ачивки.
-- tag различает уровни/вехи (25/50/x10/…) или объект (для «чистильщика» — код
-- участника, КЛИЕНТУ он не отдаётся, слепота сохраняется). Скоупится по когорте,
-- чтобы валидация (V) и эксперимент (P) считались как отдельные гонки.
CREATE TABLE IF NOT EXISTS ach_earned(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rater_id TEXT NOT NULL,
  code TEXT NOT NULL,
  tag TEXT NOT NULL DEFAULT '',
  cohort TEXT NOT NULL DEFAULT '',
  earned_at TEXT NOT NULL,
  UNIQUE(rater_id, code, tag, cohort)
);
-- Переходное состояние гонки между врачами (чтобы события «обгон»/«фотофиниш»/
-- «отрыв» срабатывали на СМЕНЕ ситуации, а не на каждом сохранении).
CREATE TABLE IF NOT EXISTS rater_state(
  rater_id TEXT NOT NULL,
  cohort TEXT NOT NULL DEFAULT '',
  leading INTEGER NOT NULL DEFAULT 0,
  max_deficit INTEGER NOT NULL DEFAULT 0,
  close INTEGER NOT NULL DEFAULT 0,
  blowout INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(rater_id, cohort)
);
"""


def init_db():
    """Создаёт таблицы при старте (idempotent) и доводит схему до актуальной."""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # Миграция существующих БД: колонки исключения добавляем мягко.
        bcols = {row[1] for row in conn.execute("PRAGMA table_info(blocks)")}
        if "voided_at" not in bcols:
            conn.execute("ALTER TABLE blocks ADD COLUMN voided_at TEXT")
        if "void_reason" not in bcols:
            conn.execute("ALTER TABLE blocks ADD COLUMN void_reason TEXT")
        # исключение отдельного повтора (бракованный/обрезанный клип)
        rcols = {row[1] for row in conn.execute("PRAGMA table_info(reps)")}
        if "voided_at" not in rcols:
            conn.execute("ALTER TABLE reps ADD COLUMN voided_at TEXT")
        if "void_reason" not in rcols:
            conn.execute("ALTER TABLE reps ADD COLUMN void_reason TEXT")
        # время первого сохранения разметки (для ачивок «серия»/«пулемёт» —
        # чтобы правка старой метки не искажала тайминги, см. _ach_times)
        acols = {row[1] for row in conn.execute("PRAGMA table_info(annotations)")}
        if "created_at" not in acols:
            conn.execute("ALTER TABLE annotations ADD COLUMN created_at TEXT")
        # Гарантия уникальности активного (участник+условие): не даёт появиться
        # двум зачётным не-исключённым блокам с одинаковым clip_id (гонка записи
        # или restore поверх пересъёмки) — иначе экспорт даёт дубли clip_id и
        # analysis.py падает. Если в БД уже есть дубли — индекс не создаётся,
        # старт не ломаем (на проде дублей нет).
        try:
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_blocks_active_cond
                            ON blocks(participant_id, condition)
                            WHERE trial=0 AND voided_at IS NULL""")
        except sqlite3.IntegrityError as e:
            print("ВНИМАНИЕ: не удалось создать ux_blocks_active_cond (дубли?):", e)
        conn.commit()
    finally:
        conn.close()


init_db()


def get_db():
    """Соединение с БД на время запроса (хранится в flask.g)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout=5000")
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def _close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cohort_sql_for(prefix):
    """SQL-довесок фильтра когорты (префикс уже валидирован по ALLOWED_PREFIXES)."""
    return f" AND b.participant_id LIKE '{prefix}%'" if prefix else ""


def req_cohort():
    """Префикс когорты из запроса (?cohort= или body.cohort), сверенный с конфигом.
    Когорты не настроены → '' (все). Невалидный/пустой → первая когорта из конфига."""
    if not ALLOWED_PREFIXES:
        return ""
    val = request.args.get("cohort")
    if not val and request.method == "POST":
        val = (request.get_json(silent=True) or {}).get("cohort")
    val = str(val or "").strip()
    if val in ALLOWED_PREFIXES:
        return val
    return str(COHORTS[0]["prefix"]) if COHORTS else ""


# ---------------------------------------------------------------- аутентификация

def find_user_by_token(token):
    """Возвращает словарь пользователя по токену или None.
    Сравнение строго через hmac.compare_digest."""
    if not isinstance(token, str) or not token:
        return None
    token_b = token.encode("utf-8")
    if hmac.compare_digest(token_b, RESEARCHER_TOKEN.encode("utf-8")):
        return {"role": "researcher", "rater_id": None, "name": "Исследователь"}
    for r in RATERS:
        if hmac.compare_digest(token_b, r["token"].encode("utf-8")):
            return {"role": "rater", "rater_id": r["id"],
                    "name": r.get("display_name") or r["name"],
                    "gender": r.get("gender", "m")}
    return None


def current_user():
    return find_user_by_token(request.cookies.get("session", ""))


def require_role(*roles):
    """Декоратор: проверяет cookie 'session' и роль. Кладёт пользователя в g.user."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if user is None:
                return jsonify({"error": "unauthorized"}), 401
            if roles and user["role"] not in roles:
                return jsonify({"error": "forbidden"}), 403
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return deco


def bad_request(msg):
    return jsonify({"error": msg}), 400


@app.errorhandler(413)
def _too_large(e):
    return jsonify({"error": "файл превышает лимит max_upload_mb"}), 413


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    token = data.get("token")
    user = find_user_by_token(token)
    if user is None:
        app.logger.warning("failed login from %s",
                           request.headers.get("X-Real-IP", request.remote_addr))
        return jsonify({"error": "invalid token"}), 401
    if user["role"] == "researcher":
        payload = {"role": "researcher", "name": "Исследователь"}
    else:
        payload = {"role": "rater", "rater_id": user["rater_id"], "name": user["name"]}
    resp = jsonify(payload)
    resp.set_cookie("session", token, httponly=True, samesite="Lax", secure=True)
    return resp


@app.route("/api/logout", methods=["POST"])
def api_logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.route("/api/me")
def api_me():
    user = current_user()
    if user is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify(user)


@app.route("/api/cohorts")
def api_cohorts():
    """Список когорт для выбора (имя + префикс). Пусто = когорты не настроены."""
    if current_user() is None:
        return jsonify({"error": "unauthorized"}), 401
    return jsonify([{"name": c.get("name", c["prefix"]), "prefix": c["prefix"]}
                    for c in COHORTS])


# ---------------------------------------------------------------- страницы

@app.route("/")
def index():
    # единая точка входа: логин с маршрутизацией по роли токена
    return send_from_directory(STATIC_DIR, "login.html")


@app.route("/record")
def page_record():
    return send_from_directory(STATIC_DIR, "record.html")


@app.route("/annotate")
def page_annotate():
    return send_from_directory(STATIC_DIR, "annotate.html")


@app.route("/dashboard")
def page_dashboard():
    # кабинет объединён со страницей записи; старые ссылки продолжают работать
    return redirect("/record", code=302)


# ---------------------------------------------------------------- блоки (исследователь)

def _int_field(value, name):
    """Строгая проверка целого (bool не считается int)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} должен быть целым числом")
    return value


def _flag01(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise ValueError(f"{name} должен быть 0 или 1")
    return value


def _new_clip_uid(db):
    """Случайный clip_uid (12 hex) с гарантией уникальности."""
    while True:
        uid = secrets.token_hex(6)
        if db.execute("SELECT 1 FROM reps WHERE clip_uid=?", (uid,)).fetchone() is None:
            return uid


@app.route("/api/blocks", methods=["POST"])
@require_role("researcher")
def api_blocks_create():
    form = request.form

    participant_id = (form.get("participant_id") or "").strip()
    if not participant_id:
        return bad_request("participant_id обязателен")
    # Если когорты настроены — код участника обязан начинаться с префикса одной из
    # них (иначе опечатка молча уведёт блок мимо когорты: не попадёт ни в очередь
    # врача, ни в экспорт нужной когорты).
    if ALLOWED_PREFIXES and not any(participant_id.startswith(p) for p in ALLOWED_PREFIXES):
        return bad_request("код участника должен начинаться с префикса когорты: "
                           + "|".join(sorted(ALLOWED_PREFIXES)))

    # Необязательный идемпотентный ключ от клиента (повторная отправка той же формы)
    client_uid = (form.get("client_uid") or "").strip()
    if len(client_uid) > 64:
        return bad_request("client_uid: не более 64 символов")

    condition = (form.get("condition") or "").strip()
    if not _valid_condition(condition):
        return bad_request("condition должен быть одним из: "
                           + "|".join(CONDITIONS) + " или NAT<номер>")

    trial_s = form.get("trial", "0")
    if trial_s not in ("0", "1"):
        return bad_request("trial должен быть '0' или '1'")
    trial = int(trial_s)

    threshold_set = (form.get("threshold_set") or "").strip()
    if not threshold_set:
        return bad_request("threshold_set обязателен")

    try:
        fps = float(form.get("fps", ""))
    except (TypeError, ValueError):
        return bad_request("fps должен быть числом")
    if not fps > 0:
        return bad_request("fps должен быть > 0")

    try:
        reps_raw = json.loads(form.get("reps") or "")
    except (TypeError, ValueError):
        return bad_request("reps: невалидный JSON")
    # Пустой список допустим: блок без распознанных повторений всё равно
    # сохраняется (сырое видео + ключевые точки), чтобы запись не терялась —
    # его можно переснять или исключить из кабинета.
    if not isinstance(reps_raw, list):
        return bad_request("reps должен быть списком")

    # Разбор и валидация повторений до записи чего-либо на диск
    parsed = []
    seen_rep_nums = set()
    try:
        for i, item in enumerate(reps_raw):
            if not isinstance(item, dict):
                raise ValueError(f"reps[{i}] должен быть объектом")
            rep_num = _int_field(item.get("rep"), f"reps[{i}].rep")
            if rep_num in seen_rep_nums:
                raise ValueError(f"reps[{i}]: повторяющийся rep {rep_num}")
            seen_rep_nums.add(rep_num)
            start_ms = _int_field(item.get("start_ms"), f"reps[{i}].start_ms")
            end_ms = _int_field(item.get("end_ms"), f"reps[{i}].end_ms")
            if start_ms < 0 or end_ms <= start_ms:
                raise ValueError(f"reps[{i}]: требуется 0 <= start_ms < end_ms")
            flags = item.get("flags")
            if not isinstance(flags, dict):
                raise ValueError(f"reps[{i}].flags должен быть объектом")
            fvals = {k: _flag01(flags.get(k), f"reps[{i}].flags.{k}") for k in FLAG_KEYS}
            parsed.append((rep_num, start_ms, end_ms, fvals))
    except ValueError as e:
        return bad_request(str(e))

    video = request.files.get("video")
    keypoints = request.files.get("keypoints")
    if video is None or keypoints is None:
        return bad_request("нужны файлы video и keypoints")

    db = get_db()

    # Идемпотентность: тот же client_uid -> возвращаем уже созданный блок без вставки
    if client_uid:
        existing = db.execute(
            """SELECT uid,
                      (SELECT COUNT(*) FROM reps WHERE block_id = blocks.id) AS reps
               FROM blocks WHERE client_uid=?""",
            (client_uid,),
        ).fetchone()
        if existing is not None:
            return jsonify({"block_uid": existing["uid"],
                            "reps": existing["reps"]}), 200

    # Уникальность «участник + условие» среди зачётных и не исключённых блоков:
    # одно и то же условие нельзя записать участнику дважды (повтор разрешён
    # только после исключения старого блока). Пробные блоки не ограничиваем.
    if trial == 0:
        dup = db.execute(
            """SELECT 1 FROM blocks
               WHERE participant_id=? AND condition=? AND trial=0
                     AND voided_at IS NULL LIMIT 1""",
            (participant_id, condition),
        ).fetchone()
        if dup is not None:
            return jsonify({"error": f"условие {condition} уже записано "
                            f"для участника {participant_id}"}), 409

    uid = secrets.token_hex(8)  # 16 hex
    # расширение исходного видео: webm с камеры или mp4/mov при загрузке файла
    video_ext = os.path.splitext(video.filename or "")[1].lower()
    if video_ext not in (".webm", ".mp4", ".mov", ".m4v"):
        video_ext = ".webm"
    video_rel = f"blocks/{uid}{video_ext}"
    jsonl_rel = f"blocks/{uid}.jsonl"
    video_abs = os.path.join(DATA_DIR, video_rel)
    jsonl_abs = os.path.join(DATA_DIR, jsonl_rel)
    video.save(video_abs)
    keypoints.save(jsonl_abs)

    def _remove_saved_files():
        for p in (video_abs, jsonl_abs):
            with contextlib.suppress(FileNotFoundError):
                os.remove(p)

    try:
        cur = db.execute(
            """INSERT INTO blocks(uid, client_uid, participant_id, condition, trial,
                                  threshold_set, fps, video_path, jsonl_path, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (uid, client_uid or None, participant_id, condition, trial, threshold_set,
             fps, video_rel, jsonl_rel, now_iso()),
        )
        block_id = cur.lastrowid
        for rep_num, start_ms, end_ms, f in parsed:
            db.execute(
                """INSERT INTO reps(block_id, rep_num, start_ms, end_ms,
                                    elbow, asymmetry, shoulder, trunk, head, clip_uid)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (block_id, rep_num, start_ms, end_ms,
                 f["elbow"], f["asymmetry"], f["shoulder"], f["trunk"], f["head"],
                 _new_clip_uid(db)),
            )
        db.commit()
    except sqlite3.IntegrityError as e:
        _remove_saved_files()
        return jsonify({"error": f"конфликт уникальности: {e}"}), 409
    except Exception:
        _remove_saved_files()
        raise
    return jsonify({"block_uid": uid, "reps": len(parsed)}), 201


@app.route("/api/blocks", methods=["GET"])
@require_role("researcher")
def api_blocks_list():
    db = get_db()
    rows = db.execute(
        """SELECT b.uid, b.participant_id, b.condition, b.trial, b.created_at,
                  b.voided_at, b.void_reason,
                  COUNT(CASE WHEN r.voided_at IS NULL THEN r.id END) AS reps,
                  COUNT(CASE WHEN r.voided_at IS NOT NULL THEN r.id END) AS reps_voided
           FROM blocks b LEFT JOIN reps r ON r.block_id = b.id
           GROUP BY b.id ORDER BY b.created_at""").fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/blocks/<uid>/reps", methods=["GET"])
@require_role("researcher")
def api_block_reps(uid):
    """Повторы блока (для исключения отдельных бракованных клипов в кабинете)."""
    db = get_db()
    blk = db.execute("SELECT id FROM blocks WHERE uid=?", (uid,)).fetchone()
    if blk is None:
        return jsonify({"error": "block not found"}), 404
    rows = db.execute(
        """SELECT rep_num, clip_uid, start_ms, end_ms, voided_at, void_reason
           FROM reps WHERE block_id=? ORDER BY rep_num""", (blk["id"],)).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------- отбраковка и удаление (исследователь)

def _audit(db, action, *, participant_id=None, block_uid=None, reason="", detail=""):
    """Запись в append-only журнал аудита. Без содержимого — только факт."""
    db.execute(
        """INSERT INTO audit_log(at, actor, action, participant_id, block_uid, reason, detail)
           VALUES(?,?,?,?,?,?,?)""",
        (now_iso(), g.user.get("name", "researcher"), action,
         participant_id, block_uid, reason, detail),
    )


def _clean_reason(raw):
    """Причина действия: непустая строка до 500 символов."""
    if not isinstance(raw, str):
        return None
    reason = raw.strip()
    if not reason or len(reason) > 500:
        return None
    return reason


@app.route("/api/blocks/<uid>/void", methods=["POST"])
@require_role("researcher")
def api_block_void(uid):
    """Исключить блок из исследования (мягко): причина обязательна, файл остаётся.
    Решение принимается по слепым, заранее заданным критериям (технический брак,
    организационная ошибка) — НЕ по тому, «правильным» ли вышел результат
    системы. Исключённый блок невидим для очереди разметки и экспортов, но
    остаётся в списке блоков и в журнале аудита для проверяемости."""
    data = request.get_json(silent=True) or {}
    reason = _clean_reason(data.get("reason"))
    if reason is None:
        return bad_request("reason обязателен (непустая строка до 500 символов)")
    db = get_db()
    row = db.execute(
        "SELECT id, participant_id, voided_at FROM blocks WHERE uid=?", (uid,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "block not found"}), 404
    if row["voided_at"] is not None:
        return jsonify({"error": "блок уже исключён"}), 409
    db.execute("UPDATE blocks SET voided_at=?, void_reason=? WHERE id=?",
               (now_iso(), reason, row["id"]))
    _audit(db, "void", participant_id=row["participant_id"], block_uid=uid, reason=reason)
    db.commit()
    return jsonify({"ok": True, "uid": uid})


@app.route("/api/blocks/<uid>/restore", methods=["POST"])
@require_role("researcher")
def api_block_restore(uid):
    """Отменить исключение блока. Тоже фиксируется в журнале аудита."""
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip() if isinstance(data.get("reason"), str) else ""
    db = get_db()
    row = db.execute(
        "SELECT id, participant_id, condition, trial, voided_at FROM blocks WHERE uid=?", (uid,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "block not found"}), 404
    if row["voided_at"] is None:
        return jsonify({"error": "блок не был исключён"}), 409
    # нельзя воскресить, если для этого (участник+условие) уже есть активный блок
    # (например, его переснимали) — иначе дубль clip_id и падение анализа.
    if row["trial"] == 0:
        dup = db.execute(
            """SELECT 1 FROM blocks WHERE participant_id=? AND condition=? AND trial=0
                     AND voided_at IS NULL AND id<>? LIMIT 1""",
            (row["participant_id"], row["condition"], row["id"]),
        ).fetchone()
        if dup is not None:
            return jsonify({"error": f"для {row['participant_id']}/{row['condition']} уже есть "
                            "активный блок; сначала исключите его"}), 409
    db.execute("UPDATE blocks SET voided_at=NULL, void_reason=NULL WHERE id=?", (row["id"],))
    _audit(db, "restore", participant_id=row["participant_id"], block_uid=uid, reason=reason)
    db.commit()
    return jsonify({"ok": True, "uid": uid})


def _rep_by_clip(db, clip_uid):
    """Повтор + код участника по clip_uid (или None)."""
    return db.execute(
        """SELECT r.id, r.voided_at, b.participant_id, b.uid AS block_uid
           FROM reps r JOIN blocks b ON b.id = r.block_id WHERE r.clip_uid=?""",
        (clip_uid,),
    ).fetchone()


@app.route("/api/reps/<clip_uid>/void", methods=["POST"])
@require_role("researcher")
def api_rep_void(clip_uid):
    """Исключить отдельный повтор (бракованный/обрезанный клип) с обязательной
    причиной. Клип уходит из очереди разметки и экспортов; обратимо, пишется в
    журнал аудита. Критерий — технический (как и для блока), не «результат не тот»."""
    data = request.get_json(silent=True) or {}
    reason = _clean_reason(data.get("reason"))
    if reason is None:
        return bad_request("reason обязателен (непустая строка до 500 символов)")
    db = get_db()
    row = _rep_by_clip(db, clip_uid)
    if row is None:
        return jsonify({"error": "rep not found"}), 404
    if row["voided_at"] is not None:
        return jsonify({"error": "повтор уже исключён"}), 409
    db.execute("UPDATE reps SET voided_at=?, void_reason=? WHERE id=?",
               (now_iso(), reason, row["id"]))
    _audit(db, "void_rep", participant_id=row["participant_id"],
           block_uid=row["block_uid"], reason=reason, detail=f"clip={clip_uid}")
    db.commit()
    return jsonify({"ok": True, "clip_uid": clip_uid})


@app.route("/api/reps/<clip_uid>/restore", methods=["POST"])
@require_role("researcher")
def api_rep_restore(clip_uid):
    """Отменить исключение повтора."""
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip() if isinstance(data.get("reason"), str) else ""
    db = get_db()
    row = _rep_by_clip(db, clip_uid)
    if row is None:
        return jsonify({"error": "rep not found"}), 404
    if row["voided_at"] is None:
        return jsonify({"error": "повтор не был исключён"}), 409
    db.execute("UPDATE reps SET voided_at=NULL, void_reason=NULL WHERE id=?", (row["id"],))
    _audit(db, "restore_rep", participant_id=row["participant_id"],
           block_uid=row["block_uid"], reason=reason, detail=f"clip={clip_uid}")
    db.commit()
    return jsonify({"ok": True, "clip_uid": clip_uid})


@app.route("/api/participants/<pid>/erase", methods=["POST"])
@require_role("researcher")
def api_participant_erase(pid):
    """Безвозвратное удаление всех данных участника (отзыв согласия, 152-ФЗ).
    Удаляет видео, ключевые точки, повторения, разметку и блоки участника.
    В журнал аудита пишется только факт удаления и счётчики — без содержимого.
    Защита от случайного нажатия: тело должно содержать confirm == pid."""
    data = request.get_json(silent=True) or {}
    reason = _clean_reason(data.get("reason"))
    if reason is None:
        return bad_request("reason обязателен (непустая строка до 500 символов)")
    if data.get("confirm") != pid:
        return bad_request("confirm должен совпадать с кодом участника")

    db = get_db()
    blocks = db.execute(
        "SELECT id, video_path, jsonl_path FROM blocks WHERE participant_id=?", (pid,)
    ).fetchall()
    if not blocks:
        return jsonify({"error": "участник не найден"}), 404

    # Удаляем строки в порядке внешних ключей: annotations -> reps -> blocks.
    n_ann = db.execute(
        """DELETE FROM annotations WHERE rep_id IN (
               SELECT r.id FROM reps r JOIN blocks b ON b.id = r.block_id
               WHERE b.participant_id=?)""", (pid,)).rowcount
    n_reps = db.execute(
        """DELETE FROM reps WHERE block_id IN (
               SELECT id FROM blocks WHERE participant_id=?)""", (pid,)).rowcount
    n_blocks = db.execute(
        "DELETE FROM blocks WHERE participant_id=?", (pid,)).rowcount

    _audit(db, "erase", participant_id=pid, reason=reason,
           detail=f"blocks={n_blocks}, reps={n_reps}, annotations={n_ann}")
    db.commit()

    # Файлы удаляем после успешного коммита БД (строки уже не ссылаются на них).
    for b in blocks:
        for rel in (b["video_path"], b["jsonl_path"]):
            with contextlib.suppress(FileNotFoundError):
                os.remove(os.path.join(DATA_DIR, rel))

    return jsonify({"ok": True, "participant_id": pid,
                    "blocks": n_blocks, "reps": n_reps, "annotations": n_ann})


# ---------------------------------------------------------------- очередь и разметка (врач)

def rater_counts(db, rater_id, cohort_sql=""):
    """(done, total) по активным (зачётным и не исключённым) повторениям когорты."""
    total = db.execute(
        f"SELECT COUNT(*) FROM reps r JOIN blocks b ON b.id=r.block_id WHERE {ACTIVE_CLIP}{cohort_sql}"
    ).fetchone()[0]
    done = db.execute(
        f"""SELECT COUNT(*) FROM annotations a
           JOIN reps r ON r.id = a.rep_id
           JOIN blocks b ON b.id = r.block_id
           WHERE a.rater_id=? AND {ACTIVE_CLIP}{cohort_sql}""",
        (rater_id,),
    ).fetchone()[0]
    return done, total


# ============================================================ ачивки (геймификация)
# Мотивирующие «ачивки» для врачей-разметчиков: серии, рывки, вехи, обгоны и т.п.
# Считаются на сервере при КАЖДОМ новом сохранении (не при правке) из того же
# набора активных клипов когорты; слепоту не нарушают (никаких participant_id /
# condition / предсказаний системы клиенту не уходит). Логику текста/анимаций
# держит фронт; сервер отдаёт только код события и числовой контекст.

ACH_STREAK_GAP = 120          # сек между клипами, пока «серия» не рвётся
ACH_SPEED_WIN = 600           # окно «пулемёта», сек (10 минут)
ACH_STREAK_TIERS = (10, 20, 30)
ACH_SPEED_TIERS = (20, 40)
ACH_DAYS_TIERS = (3, 5, 7)
ACH_DAILY_NORM = 30           # клипов за день → «дневная норма»
ACH_COMEBACK_DEFICIT = 15     # отставание, после которого обгон = «камбэк»
ACH_BLOWOUT = 25              # отрыв лидера → «контрольный»


def _ach_local(dt):
    """Сдвиг UTC-времени в локальное (только для чтения .hour/.date)."""
    return dt.astimezone(timezone.utc) + TZ_OFFSET


def _ach_local_date(dt):
    return _ach_local(dt).date()


def _ach_times(db, rater_id, cohort_sql):
    """Времена разметок врача по активным клипам когорты, в порядке создания.
    Берём created_at (момент первого сохранения), чтобы правка старой метки не
    искажала «серию»/«пулемёт»; для строк до миграции откат на updated_at."""
    rows = db.execute(
        f"""SELECT COALESCE(a.created_at, a.updated_at) FROM annotations a
            JOIN reps r ON r.id = a.rep_id
            JOIN blocks b ON b.id = r.block_id
            WHERE a.rater_id=? AND {ACTIVE_CLIP}{cohort_sql}
            ORDER BY a.id""",
        (rater_id,),
    ).fetchall()
    out = []
    for x in rows:
        try:
            out.append(datetime.fromisoformat(x[0]))
        except (ValueError, TypeError):
            pass
    return out


def _ach_trailing_streak(times):
    """Длина текущей серии: подряд идущие клипы с паузой ≤ ACH_STREAK_GAP."""
    if not times:
        return 0
    n = 1
    for i in range(len(times) - 1, 0, -1):
        delta = (times[i] - times[i - 1]).total_seconds()
        if 0 <= delta <= ACH_STREAK_GAP:   # 0<= страхует от немонотонных времён
            n += 1
        else:
            break
    return n


def _ach_burst(times):
    """Сколько клипов попало в последнее окно ACH_SPEED_WIN от последнего клипа."""
    if not times:
        return 0
    cutoff = times[-1] - timedelta(seconds=ACH_SPEED_WIN)
    return sum(1 for t in times if t >= cutoff)


def _ach_day_run(times):
    """Длина текущей серии дней подряд с разметкой (по локальной дате)."""
    if not times:
        return 0
    days = sorted({_ach_local_date(t) for t in times})
    run = 1
    for i in range(len(days) - 1, 0, -1):
        if (days[i] - days[i - 1]).days == 1:
            run += 1
        else:
            break
    return run


def _ach_today_count(times):
    """Сколько клипов размечено в локальную дату последнего клипа."""
    if not times:
        return 0
    d = _ach_local_date(times[-1])
    return sum(1 for t in times if _ach_local_date(t) == d)


def _ach_completed_participants(db, rater_id, cohort_sql):
    """Коды участников, у кого ВСЕ активные клипы размечены этим врачом.
    Используется только серверно (тег ачивки) — клиенту коды не уходят."""
    rows = db.execute(
        f"""SELECT b.participant_id AS pid,
                   COUNT(DISTINCT r.id) AS tot,
                   COUNT(DISTINCT a.rep_id) AS mine
            FROM reps r JOIN blocks b ON b.id = r.block_id
            LEFT JOIN annotations a ON a.rep_id = r.id AND a.rater_id = ?
            WHERE {ACTIVE_CLIP}{cohort_sql}
            GROUP BY b.participant_id
            HAVING tot > 0 AND mine = tot""",
        (rater_id,),
    ).fetchall()
    return [x["pid"] for x in rows]


def scoreboard(db, rater_id, cohort):
    """Табло «врач против врача» по выбранной когорте."""
    cohort_sql = cohort_sql_for(cohort)
    my, total = rater_counts(db, rater_id, cohort_sql)
    out = {
        "me": {"id": rater_id, "name": rater_display(rater_id),
               "gender": rater_gender(rater_id), "count": my},
        "rival": None,
        "total": total,
        "lead": None,
    }
    rival = rival_of(rater_id)
    if rival:
        rc = rater_counts(db, rival["id"], cohort_sql)[0]
        out["rival"] = {"id": rival["id"], "name": rater_display(rival["id"]),
                        "gender": rater_gender(rival["id"]), "count": rc}
        out["lead"] = my - rc
    return out


def evaluate_achievements(db, rater_id, cohort):
    """Начислить новые ачивки врача после нового сохранения; вернуть список
    событий для всплывающих плашек. На ПЕРВОМ вызове после запуска фичи молча
    проставляет уже заслуженное (без лавины плашек у тех, кто уже наразмечал)."""
    cohort_sql = cohort_sql_for(cohort)
    my, total = rater_counts(db, rater_id, cohort_sql)
    times = _ach_times(db, rater_id, cohort_sql)
    events = []

    earned = {(r["code"], r["tag"]) for r in db.execute(
        "SELECT code, tag FROM ach_earned WHERE rater_id=? AND cohort=?",
        (rater_id, cohort)).fetchall()}
    st_row = db.execute(
        "SELECT leading, max_deficit, close, blowout FROM rater_state "
        "WHERE rater_id=? AND cohort=?", (rater_id, cohort)).fetchone()
    first_eval = st_row is None
    st = dict(st_row) if st_row else {"leading": 0, "max_deficit": 0,
                                      "close": 0, "blowout": 0}

    def award(code, tag, **ctx):
        key = (code, str(tag))
        if key in earned:
            return
        earned.add(key)
        db.execute(
            "INSERT OR IGNORE INTO ach_earned(rater_id, code, tag, cohort, earned_at) "
            "VALUES(?,?,?,?,?)", (rater_id, code, str(tag), cohort, now_iso()))
        events.append(dict(code=code, **ctx))

    # вехи 25/50/75/100 %
    if total:
        for pct in (25, 50, 75, 100):
            if my * 100 >= pct * total:
                award("milestone", pct, value=pct, count=my, total=total)
    # серия подряд
    streak = _ach_trailing_streak(times)
    for tier in ACH_STREAK_TIERS:
        if streak >= tier:
            award("streak", tier, value=tier)
    # скоростной рывок
    burst = _ach_burst(times)
    for tier in ACH_SPEED_TIERS:
        if burst >= tier:
            award("speed", tier, value=burst)
    # дни подряд
    run = _ach_day_run(times)
    for tier in ACH_DAYS_TIERS:
        if run >= tier:
            award("days", tier, value=tier)
    # дневная норма / время суток — по последнему клипу
    if times:
        last_date = str(_ach_local_date(times[-1]))
        if _ach_today_count(times) >= ACH_DAILY_NORM:
            award("daily", last_date, value=_ach_today_count(times))
        hour = _ach_local(times[-1]).hour
        if 0 <= hour < 5:
            award("owl", last_date, hour=hour)
        elif 5 <= hour < 8:
            award("lark", last_date, hour=hour)
    # «чистильщик» — полностью закрытые участники (код участника КЛИЕНТУ не уходит)
    prior_sweeps = sum(1 for (c, _t) in earned if c == "sweep")
    new_pids = [p for p in _ach_completed_participants(db, rater_id, cohort_sql)
                if ("sweep", p) not in earned]
    for i, pid in enumerate(new_pids):
        award("sweep", pid, value=prior_sweeps + i + 1)

    # соревновательные события (нужен соперник, уже включившийся в эту когорту)
    rival = rival_of(rater_id)
    if rival:
        rc = rater_counts(db, rival["id"], cohort_sql)[0]
        if rc > 0:
            diff = my - rc
            comp = []
            if diff > 0 and not st["leading"]:
                if st["max_deficit"] >= ACH_COMEBACK_DEFICIT:
                    comp.append({"code": "comeback", "lead": diff,
                                 "deficit": st["max_deficit"]})
                else:
                    comp.append({"code": "overtake", "lead": diff})
                st["leading"] = 1
                st["max_deficit"] = 0
            elif diff <= 0:
                st["leading"] = 0
                st["max_deficit"] = max(st["max_deficit"], -diff)
            close_now = 1 if abs(diff) <= 2 else 0
            if close_now and not st["close"] and not comp:
                comp.append({"code": "photo", "lead": diff})
            st["close"] = close_now
            blow_now = 1 if diff >= ACH_BLOWOUT else 0
            if blow_now and not st["blowout"]:
                comp.append({"code": "blowout", "lead": diff})
            st["blowout"] = blow_now
            if not first_eval:
                rname, rgender = rater_display(rival["id"]), rater_gender(rival["id"])
                for e in comp:
                    e["rival_name"] = rname
                    e["rival_gender"] = rgender
                    events.append(e)
                    # одноразовый трофей в коллекцию (раздел достижений) — попап
                    # уже даёт само событие выше, второй раз не всплываем
                    db.execute(
                        "INSERT OR IGNORE INTO ach_earned(rater_id, code, tag, cohort, earned_at) "
                        "VALUES(?,?,?,?,?)", (rater_id, e["code"], "ever", cohort, now_iso()))

    db.execute(
        """INSERT INTO rater_state(rater_id, cohort, leading, max_deficit, close, blowout)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(rater_id, cohort) DO UPDATE SET
             leading=excluded.leading, max_deficit=excluded.max_deficit,
             close=excluded.close, blowout=excluded.blowout""",
        (rater_id, cohort, st["leading"], st["max_deficit"], st["close"], st["blowout"]))
    db.commit()

    if first_eval:
        return []  # молчаливая до-простановка заслуженного при первом запуске
    self_gender = rater_gender(rater_id)
    for e in events:
        e.setdefault("self_gender", self_gender)
    return events


@app.route("/api/queue")
@require_role("rater")
def api_queue():
    rater_id = g.user["rater_id"]
    cohort_sql = cohort_sql_for(req_cohort())
    db = get_db()
    rows = db.execute(
        f"""SELECT r.id AS rep_id, r.clip_uid, r.start_ms, r.end_ms, b.uid AS block_uid
           FROM reps r JOIN blocks b ON b.id = r.block_id
           WHERE {ACTIVE_CLIP}{cohort_sql} ORDER BY r.id""").fetchall()
    anns = {
        row["rep_id"]: row
        for row in db.execute(
            f"""SELECT a.rep_id, a.elbow, a.asymmetry, a.shoulder, a.trunk, a.head,
                      a.confidence, a.comments
               FROM annotations a
               JOIN reps r ON r.id = a.rep_id
               JOIN blocks b ON b.id = r.block_id
               WHERE a.rater_id=? AND {ACTIVE_CLIP}{cohort_sql}""",
            (rater_id,),
        ).fetchall()
    }

    items = []
    for row in rows:
        ann = anns.get(row["rep_id"])
        items.append({
            # НИКАКИХ participant_id / condition / rep_num / флагов системы — слепая разметка
            "clip_uid": row["clip_uid"],
            "video_url": f"/api/video/{row['block_uid']}",
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "done": ann is not None,
            "annotation": (
                {k: ann[k] for k in
                 ("elbow", "asymmetry", "shoulder", "trunk", "head", "confidence", "comments")}
                if ann is not None else None
            ),
        })

    # Детерминированный порядок, свой для каждого рейтера: новые клипы
    # встраиваются по своему хэшу, позиции старых не меняются
    items.sort(key=lambda it: hmac.new(
        rater_id.encode(), it["clip_uid"].encode(), "sha256").hexdigest())

    done = sum(1 for it in items if it["done"])
    return jsonify({
        "rater_id": rater_id,
        "total": len(items),
        "done": done,
        "items": items,
    })


@app.route("/api/annotations", methods=["POST"])
@require_role("rater")
def api_annotations():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return bad_request("ожидается JSON-объект")

    clip_uid = data.get("clip_uid")
    if not isinstance(clip_uid, str) or not clip_uid:
        return bad_request("clip_uid обязателен")

    try:
        labels = {k: _flag01(data.get(k), k) for k in FLAG_KEYS}
    except ValueError as e:
        return bad_request(str(e))

    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, int) \
            or not 1 <= confidence <= 3:
        return bad_request("confidence должен быть целым 1..3")

    comments = data.get("comments", "")
    if comments is None:
        comments = ""
    if not isinstance(comments, str):
        return bad_request("comments должен быть строкой")

    cohort = req_cohort()
    cohort_sql = cohort_sql_for(cohort)
    db = get_db()
    row = db.execute(
        f"""SELECT r.id FROM reps r JOIN blocks b ON b.id = r.block_id
           WHERE r.clip_uid=? AND {ACTIVE_CLIP}{cohort_sql}""",
        (clip_uid,),
    ).fetchone()
    if row is None:
        return jsonify({"error": "clip not found"}), 404

    rater_id = g.user["rater_id"]
    # Новое сохранение (а не правка) — только оно начисляет ачивки
    was_new = db.execute(
        "SELECT 1 FROM annotations WHERE rater_id=? AND rep_id=?",
        (rater_id, row["id"])).fetchone() is None
    # Upsert по (rater_id, rep_id). created_at ставится только при ВСТАВКЕ
    # (DO UPDATE его не трогает) — чтобы правка метки не сдвигала тайминги ачивок.
    ts = now_iso()
    db.execute(
        """INSERT INTO annotations(rater_id, rep_id, elbow, asymmetry, shoulder,
                                   trunk, head, confidence, comments, updated_at, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(rater_id, rep_id) DO UPDATE SET
             elbow=excluded.elbow, asymmetry=excluded.asymmetry,
             shoulder=excluded.shoulder, trunk=excluded.trunk, head=excluded.head,
             confidence=excluded.confidence, comments=excluded.comments,
             updated_at=excluded.updated_at""",
        (rater_id, row["id"], labels["elbow"], labels["asymmetry"],
         labels["shoulder"], labels["trunk"], labels["head"],
         confidence, comments, ts, ts),
    )
    db.commit()
    # Геймификация — строго best-effort: её сбой НИКОГДА не должен ломать
    # сохранение разметки (научный результат уже в БД). Ошибки гасим.
    events, sb = [], None
    try:
        events = evaluate_achievements(db, rater_id, cohort) if was_new else []
        sb = scoreboard(db, rater_id, cohort)
    except Exception:
        app.logger.exception("achievements eval failed (non-fatal)")
        try:
            db.rollback()
        except Exception:
            pass
        events, sb = [], None
    done, total = rater_counts(db, rater_id, cohort_sql)
    return jsonify({"ok": True, "done": done, "total": total,
                    "events": events, "scoreboard": sb})


@app.route("/api/me/stats")
@require_role("rater")
def api_me_stats():
    """Табло «врач против врача» + заслуженные ачивки для текущей когорты
    (для отрисовки при загрузке). Слепота: коды участников «чистильщика»
    наружу не отдаются — заменяются порядковым номером."""
    rater_id = g.user["rater_id"]
    cohort = req_cohort()
    db = get_db()
    earned, sweep_n = [], 0
    for r in db.execute(
            "SELECT code, tag, earned_at FROM ach_earned "
            "WHERE rater_id=? AND cohort=? ORDER BY earned_at, id",
            (rater_id, cohort)).fetchall():
        if r["code"] == "sweep":
            sweep_n += 1
            earned.append({"code": "sweep", "tag": str(sweep_n), "at": r["earned_at"]})
        else:
            earned.append({"code": r["code"], "tag": r["tag"], "at": r["earned_at"]})
    return jsonify({"scoreboard": scoreboard(db, rater_id, cohort), "earned": earned})


# ---------------------------------------------------------------- видео

@app.route("/api/video/<block_uid>")
@require_role("researcher", "rater")
def api_video(block_uid):
    db = get_db()
    row = db.execute("SELECT video_path FROM blocks WHERE uid=?", (block_uid,)).fetchone()
    if row is None:
        return jsonify({"error": "block not found"}), 404
    path = os.path.join(DATA_DIR, row["video_path"])
    if not os.path.isfile(path):
        return jsonify({"error": "video file missing"}), 404
    # conditional=True -> поддержка Range-запросов (перемотка в <video>)
    mt = "video/mp4" if path.lower().endswith((".mp4", ".mov", ".m4v")) else "video/webm"
    return send_file(path, mimetype=mt, conditional=True)


# ---------------------------------------------------------------- прогресс и экспорт (исследователь)

@app.route("/api/progress")
@require_role("researcher")
def api_progress():
    cohort_sql = cohort_sql_for(req_cohort())
    db = get_db()
    blocks = db.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
    raters_out = []
    total = 0
    for r in RATERS:
        done, total = rater_counts(db, r["id"], cohort_sql)
        raters_out.append({"rater_id": r["id"], "name": r["name"],
                           "done": done, "total": total})
    if not RATERS:
        total = db.execute(
            f"SELECT COUNT(*) FROM reps r JOIN blocks b ON b.id=r.block_id WHERE {ACTIVE_CLIP}{cohort_sql}"
        ).fetchone()[0]
    return jsonify({"raters": raters_out, "blocks": blocks, "reps": total})


def _csv_escape_cell(cell):
    """Нейтрализация CSV formula injection: ведущий апостроф у опасных строк."""
    if isinstance(cell, str) and cell.startswith(("=", "+", "-", "@", "\t")):
        return "'" + cell
    return cell


def _csv_response(header, rows, filename):
    buf = io.StringIO()
    buf.write("\ufeff")  # BOM, чтобы Excel корректно открывал UTF-8
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows([_csv_escape_cell(c) for c in row] for row in rows)
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/export/annotations/<rater_id>")
@require_role("researcher")
def api_export_annotations(rater_id):
    if rater_id not in RATERS_BY_ID:
        return jsonify({"error": "unknown rater"}), 404
    cohort_sql = cohort_sql_for(req_cohort())
    db = get_db()
    rows = db.execute(
        f"""SELECT b.participant_id, b.condition, r.rep_num,
                  a.elbow, a.asymmetry, a.shoulder, a.trunk, a.head,
                  a.confidence, a.comments
           FROM annotations a
           JOIN reps r ON r.id = a.rep_id
           JOIN blocks b ON b.id = r.block_id
           WHERE a.rater_id=? AND {ACTIVE_CLIP}{cohort_sql}
           ORDER BY b.participant_id, b.condition, r.rep_num""",
        (rater_id,),
    ).fetchall()
    out = [
        (f"{row['participant_id']}_{row['condition']}_rep{row['rep_num']}",
         row["elbow"], row["asymmetry"], row["shoulder"], row["trunk"], row["head"],
         row["confidence"], row["comments"])
        for row in rows
    ]
    return _csv_response(
        ["clip_id", "elbow", "asymmetry", "shoulder", "trunk", "head",
         "confidence", "comments"],
        out,
        f"annotations_{rater_id}.csv",
    )


@app.route("/api/export/predictions")
@require_role("researcher")
def api_export_predictions():
    cohort_sql = cohort_sql_for(req_cohort())
    db = get_db()
    rows = db.execute(
        f"""SELECT b.uid, b.participant_id, b.condition, b.threshold_set, b.fps,
                  r.rep_num, r.start_ms, r.end_ms,
                  r.elbow, r.asymmetry, r.shoulder, r.trunk, r.head
           FROM reps r JOIN blocks b ON b.id = r.block_id
           WHERE {ACTIVE_CLIP}{cohort_sql}
           ORDER BY b.participant_id, b.condition, r.rep_num""").fetchall()
    out = [
        (f"{row['participant_id']}_{row['condition']}_rep{row['rep_num']}",
         row["participant_id"], row["condition"], row["rep_num"],
         row["elbow"], row["asymmetry"], row["shoulder"], row["trunk"], row["head"],
         row["threshold_set"], row["fps"], f"{row['uid']}.jsonl",
         row["start_ms"], row["end_ms"])
        for row in rows
    ]
    return _csv_response(
        ["clip_id", "participant_id", "condition", "rep",
         "elbow", "asymmetry", "shoulder", "trunk", "head",
         "threshold_set", "fps", "keypoints_file", "rep_start_ms", "rep_end_ms"],
        out,
        "system_predictions.csv",
    )


# ---------------------------------------------------------------- запуск

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8742,
            debug=os.environ.get("FLASK_DEBUG") == "1")
