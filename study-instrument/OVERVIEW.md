# The study instrument

This is the software that produced the data in `../data/` — the browser application used
to record the sessions, the annotation interface the two clinicians worked in, and the
offline recomputation and agreement scripts. It is published so that the acquisition and
labelling procedures can be inspected rather than taken on description.

It is **not** the deployed rehabilitation product. The paper evaluates a pipeline whose
metric definitions, thresholds and segmentation are those given in the manuscript; this
repository holds the research build of that pipeline, which additionally carries the upload,
authentication and annotation machinery a study needs and a deployed application does not.
The distinction matters for the privacy discussion in the paper: the deployed configuration
keeps derived data in the page session, whereas this instrument deliberately uploaded video
and keypoints to a laboratory server, because blinded annotation requires clinicians to
watch the movement.

Most of the documentation and interface text is in Russian, the working language of the
study. English descriptions of the protocol and of the annotation task are in `../docs/`.

## What is here

| Path | What it is |
|---|---|
| `webapp/server.py` | The study server: session and block management, upload endpoints, token-gated annotation, export |
| `webapp/static/record.html` | Recording interface — pose estimation, live metrics, repetition segmentation |
| `webapp/static/annotate.html` | Annotation interface used by the two clinicians |
| `webapp/static/login.html` | Token login |
| `webapp/recompute.py` | Offline recomputation of the flags from stored keypoints — the canonical definition of every metric |
| `webapp/kappa.py` | Inter-rater agreement |
| `analysis.py` | Detection metrics and the threshold sweep |
| `protocol.md` | Recording protocol as executed |
| `annotator_instructions.md` | Instructions given to the annotating clinicians |
| `templates/system_predictions.csv` | Column template, with dummy rows |
| `webapp/Dockerfile`, `docker-compose.yml` | How the server was run |

`webapp/recompute.py` is the file to read first if the question is *what exactly does the
system compute*: it defines the landmark indices, the geometry of all five metrics, the
resting-baseline estimate, and the 250 ms persistence rule. The published tables in
`../data/repetitions.csv` were derived with that same logic, in the corrected isotropic
coordinate convention (see `../code/extract_repetition_tables.py`).

## What is deliberately absent

- **`webapp/config.json`** — the live configuration. It held the access tokens of the
  researcher and of the two annotating clinicians, together with their given names. Only
  `config.example.json`, with placeholders, is published. The tokens were never committed
  to the source history.
- **`webapp/data/`** — the working directory of the running server: the session database and
  the uploaded recordings. The derived, repetition-level results are published in `../data/`;
  the video and the per-frame keypoints are not (see `../docs/privacy.md`).
- **`webapp/venv/`** — a local virtual environment. `requirements.txt` lists what is needed.
- The signed consent documentation. Participation was documented on the institutional
  informed-consent form of the Federal Center of Brain Research and Neurotechnologies
  (order No. 117 of 1 June 2020), as stated in the paper; that form is not ours to publish,
  and no draft template is included here in order not to be mistaken for it.

## Running it

```bash
cd webapp
cp config.example.json config.json     # then set real tokens
pip install -r requirements.txt
python3 server.py                      # or: docker compose up
```

The application needs a camera and a browser with WebGL. It writes to `data/` beside the
server file.

## Licence

MIT, as for the rest of the code in this repository (`../LICENSE`).
