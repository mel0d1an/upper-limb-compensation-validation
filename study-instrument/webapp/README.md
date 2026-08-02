# Study web application: volunteer recording and blind video annotation

A minimalist Flask application for running the study: the researcher records
blocks of volunteer exercises (video + keypoints), expert clinicians annotate
the repetitions blindly, and the researcher exports CSVs for analysis
(`study/analysis.py`).

## Contents

1. [Project layout](#1-project-layout)
2. [Local run for testing](#2-local-run-for-testing)
3. [Deployment on a VPS (Ubuntu 22.04+)](#3-deployment-on-a-vps-ubuntu-2204)
4. [Privacy and protocol requirements](#4-privacy-and-protocol-requirements)
5. [Study workflow](#5-study-workflow)
6. [Endpoint table](#6-endpoint-table)

---

## 1. Project layout

```
webapp/
├── server.py              # the whole backend: Flask app, SQLite, API
├── static/
│   ├── login.html         # single login page (routing by token role)
│   ├── record.html        # researcher workspace: recording, video upload, statistics
│   └── annotate.html      # blind-annotation page (clinicians)
├── Dockerfile             # container: python:3.12-slim + waitress
├── docker-compose.yml     # port 127.0.0.1:8742, volumes for config.json (ro) and data/
├── config.example.json    # configuration template (kept in git)
├── config.json            # real configuration with tokens (NOT in git)
├── requirements.txt       # flask, waitress
└── data/                  # created by the server; NOT in git
    ├── study.db           # SQLite: blocks / reps / annotations
    └── blocks/            # <uid>.webm|.mp4 and <uid>.jsonl
```

Two roles — and two pages:

- **static/record.html** — the researcher workspace (vanilla JS,
  Russian-language interface). Everything in one window: live recording from
  the camera (MediaPipe, repetition segmentation, submission to
  `POST /api/blocks`), **upload of a pre-recorded video file**
  (.mp4/.webm/.mov — the file is run through the same pipeline with the same
  frozen thresholds right in the browser, then uploaded as a regular block),
  and statistics: each clinician's progress with export of their
  `annotations_<id>.csv`, a `system_predictions.csv` button, a table of
  recorded blocks (auto-refreshing).
  Two **recording modes**: "Healthy — 6 conditions" (scripted
  COR/ELB/ASY/SHR/TRK/HED, one condition per participant — a repeat is
  rejected by the server with 409, a condition progress bar and auto-advance
  to the next id after 6/6) and "Patient — natural" (no script, the condition
  is written as `NAT1`, `NAT2`, … by trial number so that `clip_id` values
  stay unique; no limit of 6). Uniqueness of "participant + condition" among
  counted, non-excluded blocks is guaranteed by the server.
- **static/annotate.html** — the clinician page: a queue of clips in an
  individually shuffled order, with no information about the participant,
  condition or system predictions (blind annotation).

`/dashboard` has been retired and redirects to `/record` (the workspace is
merged into the recording page).
- **config.json** — access tokens and parameters. Stored only on the server,
  permissions `600`. Only `config.example.json` is in the repository.
- **data/** — all study data (video = personal data, see section 4).

## 2. Local run for testing

```bash
cd study/webapp

# 1. Virtual environment and dependencies
python3 -m venv venv
. venv/bin/activate
pip install -r requirements.txt

# 2. Configuration
cp config.example.json config.json

# 3. Generate long random tokens (one per role)
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # researcher_token
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # clinician R1 token
python3 -c "import secrets; print(secrets.token_urlsafe(24))"   # clinician R2 token
# put them into config.json in place of CHANGE-ME-...

# 4. Run (debug mode, listens on 127.0.0.1:8742 only)
python3 server.py
```

Open <http://127.0.0.1:8742/> in the browser — this is the **single login
page**: enter a token and the system routes you by role (researcher token →
`/record` workspace, clinician token → `/annotate` annotation). Opening
`/record` or `/annotate` directly without a session returns you to the login
page. Record a test block, then log in with a clinician token in a private
window — the clip should appear in the annotation queue.

> To record video the browser needs camera access: it is allowed on
> `localhost`, but on a remote server the camera works **only over HTTPS**
> (a browser requirement for `getUserMedia`). One more reason HTTPS is
> mandatory (section 3.4).

## 3. Deployment on a VPS (Ubuntu 22.04+)

Two equivalent paths: **Docker (recommended — section 3.0)** or manually via
systemd (sections 3.1–3.2). In both cases only nginx with HTTPS faces the
outside (sections 3.3–3.4).

### 3.0. Option A: Docker (recommended)

On the VPS you only need Docker + the compose plugin (`apt install docker.io
docker-compose-v2` or the official docker.com script).

```bash
# application code to the server (the whole study/webapp folder)
scp -r study/webapp user@vps:~/study-webapp && ssh user@vps
cd ~/study-webapp

# configuration with production tokens
cp config.example.json config.json
python3 -c "import secrets; print(secrets.token_urlsafe(24))"  # x3, fill in
nano config.json && chmod 600 config.json

# data directory: uid 1000 writes inside the container
mkdir -p data && sudo chown 1000:1000 data

docker compose up -d --build
docker compose logs -f   # make sure the server came up
```

The container listens only on the host's `127.0.0.1:8742` (see
`docker-compose.yml`) — public access strictly through nginx with HTTPS
(sections 3.3–3.4, unchanged). `config.json` is mounted read-only, the data
live in `./data` on the host (backup and deletion on completion — section 4).
Updating the application: `git pull`/`scp` the new files →
`docker compose up -d --build`.

### 3.1. Option B, manual: non-root user and application code

The application runs as a separate unprivileged user:

```bash
sudo adduser --system --group --home /opt/studyapp studyapp

sudo -u studyapp mkdir -p /opt/studyapp/webapp
# copy server.py, static/, requirements.txt, config.example.json:
sudo rsync -a --chown=studyapp:studyapp study/webapp/ /opt/studyapp/webapp/ \
  --exclude venv --exclude data --exclude config.json

cd /opt/studyapp/webapp
sudo -u studyapp python3 -m venv venv
sudo -u studyapp venv/bin/pip install -r requirements.txt

sudo -u studyapp cp config.example.json config.json
# put the generated tokens into config.json (see section 2)
sudo chmod 600 config.json
```

Manual check (the server listens on localhost only — nginx exposes it):

```bash
sudo -u studyapp /opt/studyapp/webapp/venv/bin/waitress-serve \
  --listen=127.0.0.1:8742 server:app
```

### 3.2. systemd unit

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

# Privilege minimization
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
journalctl -u studyapp -f        # logs
```

### 3.3. nginx reverse proxy

```bash
sudo apt install nginx
```

`/etc/nginx/sites-available/studyapp` (replace `study.example.org` with your domain):

```nginx
server {
    listen 80;
    server_name study.example.org;

    # slightly above max_upload_mb (300 MB in config.json), so that the
    # informative JSON about an oversized upload comes from the application,
    # not from nginx's own 413 page
    client_max_body_size 310m;

    location / {
        proxy_pass http://127.0.0.1:8742;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # large uploads and streaming video delivery
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

### 3.4. HTTPS via certbot — MANDATORY

Access tokens and volunteer videos (personal data) travel over the channel, so
running over plain HTTP is unacceptable. Besides, without HTTPS the browser
will not grant camera access on the recording page.

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d study.example.org --redirect
```

`--redirect` sets up automatic HTTP → HTTPS redirection; certbot renews the
certificate on a timer by itself. Check renewal: `sudo certbot renew --dry-run`.

After the certificate is issued, **be sure** to add the HSTS header to the
HTTPS `server` block:

```nginx
add_header Strict-Transport-Security "max-age=31536000" always;
```

## 4. Privacy and protocol requirements

Volunteer videos are **personal data** (biometrically identifying images of
people). The study protocol requires de-identified storage, access restricted
to the research group, and blind annotation. Minimal measures:

- **HTTPS is mandatory.** No access to the application over HTTP; the redirect
  to HTTPS is enabled (section 3.4).
- **Tokens — long, random, individual.** Each clinician gets their own token
  (`secrets.token_urlsafe(24)` or longer), plus a separate researcher token.
  Hand tokens over via secure channels, not open e-mail. On suspected
  compromise — change them in `config.json` and restart the service.
- **Session cookie — HttpOnly, SameSite=Lax** (implemented in `server.py`):
  the token is not reachable from JavaScript.
- **De-identification in the annotation interface.** A clinician sees only a
  random `clip_uid`; `participant_id`, condition, repetition number and system
  predictions are not exposed to clinicians in any endpoint. Trade-off: the
  video is served as a whole block via the shared `GET /api/video/<block_uid>`,
  so a clinician can technically tell that several clips belong to one block
  (and therefore to one condition). This is a deliberate trade-off for serving
  the video as a whole block without re-encoding; a possible improvement is to
  cut the block into individual clips at upload time (ffmpeg) and serve each
  clip under its own uid.
- **VPS disk encryption** — desirable (LUKS / provider-side encryption), so
  the data are unreadable if the medium is seized or decommissioned.
- **Server access — SSH keys only**: `PasswordAuthentication no`,
  `PermitRootLogin no` in `/etc/ssh/sshd_config`; the firewall (ufw) opens
  only 22, 80, 443.
- **Backups of `data/` — encrypted archives only**, for example:

  ```bash
  tar -C /opt/studyapp/webapp -cz data | \
    gpg --symmetric --cipher-algo AES256 -o study-data-$(date +%F).tar.gz.gpg
  ```

  Keep copies only on media held by the research group.
- **Rejection and deletion — with an audit trail.** A defective block (camera
  failure, framing, wrong participant code, a bystander in the frame) is
  *excluded* by the researcher (`/api/blocks/<uid>/void`) with a mandatory
  reason: the block disappears from the annotation queue and the exports, but
  the file is kept, and the action is written to the audit log (the
  `audit_log` table) and is reversible (`/restore`). The exclusion criterion
  is technical/organizational, **not** whether the system's result came out
  "right" (that would be accuracy tuning). A participant's withdrawal of
  consent is executed via `/api/participants/<pid>/erase` — irreversible
  deletion of the video, keypoints, annotations and blocks; only the fact and
  the counts are written to the audit log (no content). This is the technical
  implementation of the right to erasure promised in the consent form
  (Russian Federal Law 152-FZ).
- **Data stay on the VPS only for the annotation period.** Once annotation and
  CSV export are finished, the `data/` directory is irreversibly deleted from
  the server (`shred`/`rm`; with an encrypted disk, destroying the key is
  enough) and the service is stopped. The VPS is a temporary venue, not the
  study archive.
- **Hosting jurisdiction** is agreed with the ethics committee's requirements.
  For Russia, Federal Law 152-FZ applies: personal data of Russian citizens
  must be stored on servers located in Russia — choose a Russian provider.
- **Participant consent** must explicitly cover processing of the video
  recordings on a rented server (stating the protection measures and the
  storage period).

## 5. Study workflow

1. **Recording blocks.** The researcher opens `https://<domain>/record`, logs
   in with the `researcher_token`, and records blocks (video + keypoints +
   repetition boundaries with system flags). The page itself submits the block
   to `POST /api/blocks`.

   Already-recorded files can be uploaded manually with curl:

   ```bash
   # 1) log in, save the session cookie
   curl -s -c cookies.txt -H 'Content-Type: application/json' \
     -d '{"token":"<researcher_token>"}' \
     https://study.example.org/api/login

   # 2) upload a block
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

   Uploads are idempotent: re-sending a block with the same `client_uid` does
   not create a duplicate — useful to know when uploading manually with curl
   (for example, when retrying a request after a dropped connection).

   Blocks with `trial=1` are practice runs: they enter neither the annotation
   queue nor the exports.

2. **Monitoring.** At the bottom of the same `/record` page: each clinician's
   progress, the block list, one-click CSV export. The same is available via
   the API: `GET /api/progress`, `GET /api/blocks`.

3. **Annotation.** Clinicians open `https://<domain>/annotate`, log in with
   their tokens and annotate the clips: 5 binary labels
   (elbow/asymmetry/shoulder/trunk/head), confidence 1–3, a comment. Each
   clinician gets their own clip order (deterministic shuffling), a repeated
   login resumes where they stopped, and annotations can be corrected
   (upsert).

4. **Export.** When both clinicians are done:

   ```bash
   curl -b cookies.txt -o annotations_R1.csv https://study.example.org/api/export/annotations/R1
   curl -b cookies.txt -o annotations_R2.csv https://study.example.org/api/export/annotations/R2
   curl -b cookies.txt -o system_predictions.csv https://study.example.org/api/export/predictions
   ```

   In the exports, `clip_id` is already decoded to its real form
   `{participant_id}_{condition}_rep{rep_num}` (for example, `P07_ELB_rep1`).

5. **Analysis.** The exported CSVs go into `study/analysis.py`: inter-rater
   agreement (kappa) → consensus labels → evaluation of the system
   predictions (evaluate).

6. **Completion.** The data are deleted from the VPS (section 4) and the
   service is stopped.

## 6. Endpoint table

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/` | — | single login: token → redirect by role |
| GET | `/dashboard` | — | 302 → `/record` (workspace merged) |
| GET | `/record` | — | researcher workspace (no session → `/`) |
| GET | `/annotate` | — | annotation page (no session → `/`) |
| POST | `/api/login` | — | log in by token; sets the HttpOnly `session` cookie |
| POST | `/api/logout` | any | log out, clear the cookie |
| GET | `/api/me` | any | current role/name or 401 |
| POST | `/api/blocks` | researcher | upload a block (multipart: fields + webm + jsonl) |
| GET | `/api/blocks` | researcher | list of blocks (with excluded ones marked, incl. the reason) |
| POST | `/api/blocks/<uid>/void` | researcher | exclude a block (`reason` mandatory); the file is kept, the block leaves the queue and the exports |
| POST | `/api/blocks/<uid>/restore` | researcher | undo a block's exclusion |
| POST | `/api/participants/<pid>/erase` | researcher | irreversibly delete all of a participant's data (`reason` + `confirm`==pid) |
| GET | `/api/queue` | rater | personal shuffled clip queue (no de-anonymizing fields) |
| GET | `/api/video/<block_uid>` | any | the block's webm video (with Range support) |
| POST | `/api/annotations` | rater | save/update a clip's annotation |
| GET | `/api/progress` | researcher | clinicians' progress, block/repetition counts |
| GET | `/api/export/annotations/<rater_id>` | researcher | a clinician's annotation CSV |
| GET | `/api/export/predictions` | researcher | system-prediction CSV |
