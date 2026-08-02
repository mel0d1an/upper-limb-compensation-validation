# Веб-приложение исследования: запись добровольцев и слепая разметка видео

Минималистичное Flask-приложение для проведения исследования: исследователь записывает
блоки упражнений добровольцев (видео + ключевые точки), врачи-эксперты вслепую размечают
повторения, исследователь выгружает CSV для анализа (`study/analysis.py`).

## Содержание

1. [Состав проекта](#1-состав-проекта)
2. [Локальный запуск для проверки](#2-локальный-запуск-для-проверки)
3. [Развёртывание на VPS (Ubuntu 22.04+)](#3-развёртывание-на-vps-ubuntu-2204)
4. [Приватность и требования протокола](#4-приватность-и-требования-протокола)
5. [Рабочий цикл исследования](#5-рабочий-цикл-исследования)
6. [Таблица эндпоинтов](#6-таблица-эндпоинтов)

---

## 1. Состав проекта

```
webapp/
├── server.py              # весь бэкенд: Flask-приложение, SQLite, API
├── static/
│   ├── login.html         # единая страница входа (маршрутизация по роли токена)
│   ├── record.html        # кабинет исследователя: запись, загрузка видео, статистика
│   └── annotate.html      # страница слепой разметки (врачи)
├── Dockerfile             # контейнер: python:3.12-slim + waitress
├── docker-compose.yml     # порт 127.0.0.1:8742, тома config.json (ro) и data/
├── config.example.json    # шаблон конфигурации (лежит в git)
├── config.json            # реальная конфигурация с токенами (НЕ в git)
├── requirements.txt       # flask, waitress
└── data/                  # создаётся сервером; НЕ в git
    ├── study.db           # SQLite: blocks / reps / annotations
    └── blocks/            # <uid>.webm|.mp4 и <uid>.jsonl
```

Ролей две — и страницы две:

- **static/record.html** — кабинет исследователя (vanilla JS, русский интерфейс).
  Всё в одном окне: живая запись с камеры (MediaPipe, разметка повторений,
  отправка на `POST /api/blocks`), **загрузка готового видеофайла**
  (.mp4/.webm/.mov — файл прогоняется через тот же конвейер с теми же
  замороженными порогами прямо в браузере, затем загружается как обычный
  блок), и статистика: прогресс каждого врача с выгрузкой его
  `annotations_<id>.csv`, кнопка `system_predictions.csv`, таблица
  записанных блоков (автообновление).
  Два **режима записи**: «Здоровый — 6 условий» (сценарные COR/ELB/ASY/SHR/TRK/HED,
  одно условие на участника — повтор запрещён сервером 409, прогресс-бар условий
  и авто-переход к следующему id после 6/6) и «Пациент — естественно» (без
  сценария, условие пишется как `NAT1`, `NAT2`, … по номеру пробы, чтобы
  `clip_id` оставались уникальными; ограничения 6 нет). Уникальность
  «участник+условие» среди зачётных и не исключённых блоков гарантирует сервер.
- **static/annotate.html** — страница врача: очередь клипов в индивидуальном
  перемешанном порядке, без какой-либо информации об участнике, условии или
  предсказаниях системы (слепая разметка).

`/dashboard` упразднён и редиректит на `/record` (кабинет объединён со
страницей записи).
- **config.json** — токены доступа и параметры. Хранится только на сервере,
  права `600`. В репозитории лежит лишь `config.example.json`.
- **data/** — все данные исследования (видео = персональные данные, см. раздел 4).

## 2. Локальный запуск для проверки

```bash
cd study/webapp

# 1. Виртуальное окружение и зависимости
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt

# 2. Конфигурация
cp config.example.json config.json

# 3. Сгенерировать длинные случайные токены (по одному на каждую роль)
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # researcher_token
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # токен врача R1
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # токен врача R2
# вписать их в config.json вместо CHANGE-ME-...

# 4. Запуск (отладочный режим, слушает только 127.0.0.1:8742)
python3 server.py
```

Открыть в браузере <http://127.0.0.1:8742/> — это **единая страница входа**:
введите токен, и система сама направит вас по роли (токен исследователя →
кабинет `/record`, токен врача → разметка `/annotate`). Прямое открытие
`/record` или `/annotate` без сессии возвращает на страницу входа.
Запишите тестовый блок, затем в приватном окне войдите токеном врача — клип
должен появиться в очереди разметки.

> Для записи видео браузеру нужен доступ к камере: на `localhost` он разрешён,
> на удалённом сервере камера работает **только по HTTPS** (требование браузеров
> к `getUserMedia`). Ещё одна причина, почему HTTPS обязателен (раздел 3.4).

## 3. Развёртывание на VPS (Ubuntu 22.04+)

Два равнозначных пути: **Docker (рекомендуется — раздел 3.0)** или
вручную через systemd (разделы 3.1–3.2). В обоих случаях наружу смотрит
только nginx с HTTPS (разделы 3.3–3.4).

### 3.0. Вариант A: Docker (рекомендуется)

На VPS достаточно Docker + плагина compose (`apt install docker.io
docker-compose-v2` или официальный скрипт docker.com).

```bash
# код приложения на сервер (папка study/webapp целиком)
scp -r study/webapp user@vps:~/study-webapp && ssh user@vps
cd ~/study-webapp

# конфигурация с боевыми токенами
cp config.example.json config.json
python3 -c "import secrets; print(secrets.token_urlsafe(24))"  # x3, вписать
nano config.json && chmod 600 config.json

# каталог данных: в контейнере пишет uid 1000
mkdir -p data && sudo chown 1000:1000 data

docker compose up -d --build
docker compose logs -f   # убедиться, что сервер поднялся
```

Контейнер слушает только `127.0.0.1:8742` хоста (см. `docker-compose.yml`) —
публичный доступ строго через nginx с HTTPS (разделы 3.3–3.4 без изменений).
`config.json` монтируется только на чтение, данные живут в `./data` на хосте
(бэкап и удаление по завершении — раздел 4). Обновление приложения:
`git pull`/`scp` новых файлов → `docker compose up -d --build`.

### 3.1. Вариант B вручную: пользователь без root и код приложения

Приложение работает от отдельного непривилегированного пользователя:

```bash
sudo adduser --system --group --home /opt/studyapp studyapp

sudo -u studyapp mkdir -p /opt/studyapp/webapp
# скопировать server.py, static/, requirements.txt, config.example.json:
sudo rsync -a --chown=studyapp:studyapp study/webapp/ /opt/studyapp/webapp/ \
  --exclude venv --exclude data --exclude config.json

cd /opt/studyapp/webapp
sudo -u studyapp python3 -m venv venv
sudo -u studyapp venv/bin/pip install -r requirements.txt

sudo -u studyapp cp config.example.json config.json
# вписать в config.json сгенерированные токены (см. раздел 2)
sudo chmod 600 config.json
```

Проверка вручную (сервер слушает только localhost — наружу его отдаёт nginx):

```bash
sudo -u studyapp /opt/studyapp/webapp/venv/bin/waitress-serve \
  --listen=127.0.0.1:8742 server:app
```

### 3.2. systemd-юнит

`/etc/systemd/system/studyapp.service`:

```ini
[Unit]
Description=Study webapp (recording + blind annotation)
After=network.target

[Service]
Type=simple
User=studyapp
Group=studyapp
WorkingDirectory=/opt/studyapp/webapp
ExecStart=/opt/studyapp/webapp/venv/bin/waitress-serve --listen=127.0.0.1:8742 server:app
Restart=on-failure
RestartSec=3

# Минимизация привилегий
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/studyapp/webapp/data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now studyapp
sudo systemctl status studyapp
journalctl -u studyapp -f        # логи
```

### 3.3. nginx reverse proxy

```bash
sudo apt install nginx
```

`/etc/nginx/sites-available/studyapp` (замените `study.example.org` на ваш домен):

```nginx
server {
    listen 80;
    server_name study.example.org;

    # чуть больше max_upload_mb (300 МБ в config.json), чтобы информативный
    # JSON о превышении отдавало приложение, а не nginx своей страницей 413
    client_max_body_size 310m;

    location / {
        proxy_pass http://127.0.0.1:8742;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # большие загрузки и потоковая отдача видео
        proxy_request_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/studyapp /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

### 3.4. HTTPS через certbot — ОБЯЗАТЕЛЬНО

По каналу передаются токены доступа и видео добровольцев (персональные данные),
поэтому работа по голому HTTP недопустима. Кроме того, без HTTPS браузер не даст
доступ к камере на странице записи.

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d study.example.org --redirect
```

`--redirect` настраивает автоматическую переадресацию HTTP → HTTPS; certbot сам
обновляет сертификат по таймеру. Проверить продление: `sudo certbot renew --dry-run`.

После выпуска сертификата **обязательно** добавьте в HTTPS-`server`-блок заголовок HSTS:

```nginx
add_header Strict-Transport-Security "max-age=31536000" always;
```

## 4. Приватность и требования протокола

Видео добровольцев — **персональные данные** (биометрически идентифицирующие
изображения людей). Протокол исследования требует деидентифицированного хранения,
доступа только у исследовательской группы и слепой разметки. Минимальные меры:

- **HTTPS обязателен.** Никакого доступа к приложению по HTTP; редирект на HTTPS
  включён (раздел 3.4).
- **Токены — длинные, случайные, индивидуальные.** Каждому врачу свой токен
  (`secrets.token_urlsafe(24)` и длиннее), плюс отдельный токен исследователя.
  Токены передавать по защищённым каналам, не по открытой почте. При подозрении
  на компрометацию — сменить в `config.json` и перезапустить сервис.
- **Cookie сессии — HttpOnly, SameSite=Lax** (реализовано в `server.py`):
  токен недоступен из JavaScript.
- **Деидентификация в интерфейсе разметки.** Врач видит только случайный
  `clip_uid`; `participant_id`, условие, номер повторения и предсказания системы
  врачам не передаются ни в каком эндпоинте. Компромисс: видео отдаётся целым
  блоком через общий `GET /api/video/<block_uid>`, поэтому врач технически может
  понять, что несколько клипов принадлежат одному блоку (а значит — одному
  условию). Это осознанный компромисс ради отдачи видео целым блоком без
  перекодирования; возможная доработка — нарезать блок на отдельные клипы при
  загрузке (ffmpeg) и отдавать каждый клип по собственному uid.
- **Шифрование диска VPS** — желательно (LUKS / шифрование на стороне провайдера),
  чтобы данные не были читаемы при изъятии или утилизации носителя.
- **Доступ к серверу — только по SSH-ключам**: `PasswordAuthentication no`,
  `PermitRootLogin no` в `/etc/ssh/sshd_config`; файрвол (ufw) открывает только
  22, 80, 443.
- **Бэкап `data/` — только шифрованным архивом**, например:

  ```bash
  tar -C /opt/studyapp/webapp -cz data | \
    gpg --symmetric --cipher-algo AES256 -o study-data-$(date +%F).tar.gz.gpg
  ```

  Хранить копии только на носителях исследовательской группы.
- **Отбраковка и удаление — с аудитом.** Бракованный блок (сбой камеры,
  кадрирование, не тот код участника, посторонний в кадре) исследователь
  *исключает* (`/api/blocks/<uid>/void`) с обязательной причиной: блок исчезает
  из очереди разметки и экспортов, но файл сохраняется, а действие пишется в
  журнал аудита (таблица `audit_log`) и обратимо (`/restore`). Критерий
  исключения — технический/организационный, **не** «правильным» ли вышел
  результат системы (иначе это подгонка точности). Отзыв согласия участником
  выполняется через `/api/participants/<pid>/erase` — безвозвратное удаление
  видео, ключевых точек, разметки и блоков; в журнал аудита пишется только факт
  и счётчики (без содержимого). Это и есть техническая реализация обещанного в
  форме согласия права на удаление (152-ФЗ).
- **Данные на VPS — только на период разметки.** По завершении разметки и выгрузки
  CSV каталог `data/` удаляется с сервера безвозвратно (`shred`/`rm` + при
  шифрованном диске достаточно уничтожения ключа), сервис останавливается.
  VPS — временная площадка, не архив исследования.
- **Юрисдикция хостинга** согласуется с требованиями этического комитета.
  Для РФ действует 152-ФЗ: персональные данные граждан РФ должны храниться
  на серверах на территории РФ — выбирайте российского провайдера.
- **Согласие участников** должно явно покрывать обработку видеозаписей на
  арендованном сервере (с указанием мер защиты и срока хранения).

## 5. Рабочий цикл исследования

1. **Запись блоков.** Исследователь открывает `https://<домен>/record`, входит по
   `researcher_token`, записывает блоки (видео + ключевые точки + границы
   повторений с флагами системы). Страница сама отправляет блок на
   `POST /api/blocks`.

   Уже записанные файлы можно загрузить вручную через curl:

   ```bash
   # 1) логин, сохранить cookie сессии
   curl -s -c cookies.txt -H 'Content-Type: application/json' \
     -d '{"token":"<researcher_token>"}' \
     https://study.example.org/api/login

   # 2) загрузка блока
   curl -s -b cookies.txt -X POST https://study.example.org/api/blocks \
     -F participant_id=P07 \
     -F condition=ELB \
     -F trial=0 \
     -F threshold_set=v1.0-frozen-20260612 \
     -F fps=30 \
     -F 'reps=[{"rep":1,"start_ms":1234,"end_ms":5678,"flags":{"elbow":0,"asymmetry":0,"shoulder":1,"trunk":0,"head":0}}]' \
     -F video=@P07_ELB.webm \
     -F keypoints=@P07_ELB.jsonl
   # -> 201 {"block_uid": "...", "reps": 1}
   ```

   Загрузка идемпотентна: повторная отправка блока с тем же `client_uid` не
   создаёт дубликат — полезно знать при ручной загрузке через curl (например,
   при повторе запроса после обрыва соединения).

   Блоки с `trial=1` — тренировочные: в очередь разметки и в экспорты не попадают.

2. **Мониторинг.** Внизу той же страницы `/record`: прогресс
   каждого врача, список блоков, выгрузка CSV в один клик. То же доступно по
   API: `GET /api/progress`, `GET /api/blocks`.

3. **Разметка.** Врачи открывают `https://<домен>/annotate`, входят своим токеном
   и размечают клипы: 5 бинарных меток (elbow/asymmetry/shoulder/trunk/head),
   уверенность 1–3, комментарий. Порядок клипов у каждого врача свой
   (детерминированное перемешивание), повторный вход продолжает с места остановки,
   разметку можно исправлять (upsert).

4. **Выгрузка.** Когда оба врача закончили:

   ```bash
   curl -b cookies.txt -o annotations_R1.csv https://study.example.org/api/export/annotations/R1
   curl -b cookies.txt -o annotations_R2.csv https://study.example.org/api/export/annotations/R2
   curl -b cookies.txt -o system_predictions.csv https://study.example.org/api/export/predictions
   ```

   В экспортах `clip_id` уже расшифрован в реальный вид
   `{participant_id}_{condition}_rep{rep_num}` (например, `P07_ELB_rep1`).

5. **Анализ.** Выгруженные CSV подаются в `study/analysis.py`:
   межэкспертная согласованность (kappa) → консенсусные метки → оценка
   предсказаний системы (evaluate).

6. **Завершение.** Данные удаляются с VPS (раздел 4), сервис останавливается.

## 6. Таблица эндпоинтов

| Метод | Путь | Роль | Назначение |
|---|---|---|---|
| GET | `/` | — | единый вход: токен → редирект по роли |
| GET | `/dashboard` | — | 302 → `/record` (кабинет объединён) |
| GET | `/record` | — | кабинет исследователя (без сессии → `/`) |
| GET | `/annotate` | — | страница разметки (без сессии → `/`) |
| POST | `/api/login` | — | вход по токену; ставит HttpOnly-cookie `session` |
| POST | `/api/logout` | любая | выход, очистка cookie |
| GET | `/api/me` | любая | текущая роль/имя или 401 |
| POST | `/api/blocks` | researcher | загрузка блока (multipart: поля + webm + jsonl) |
| GET | `/api/blocks` | researcher | список блоков (с пометкой исключённых и причиной) |
| POST | `/api/blocks/<uid>/void` | researcher | исключить блок (обязательна `reason`); файл сохраняется, блок уходит из очереди и экспортов |
| POST | `/api/blocks/<uid>/restore` | researcher | отменить исключение блока |
| POST | `/api/participants/<pid>/erase` | researcher | безвозвратно удалить все данные участника (`reason` + `confirm`==pid) |
| GET | `/api/queue` | rater | персональная перемешанная очередь клипов (без деанонимирующих полей) |
| GET | `/api/video/<block_uid>` | любая | webm-видео блока (с поддержкой Range) |
| POST | `/api/annotations` | rater | сохранить/обновить разметку клипа |
| GET | `/api/progress` | researcher | прогресс врачей, число блоков/повторений |
| GET | `/api/export/annotations/<rater_id>` | researcher | CSV разметки врача |
| GET | `/api/export/predictions` | researcher | CSV предсказаний системы |
