<div align="center">

<img src="frontend/public/brand/mark.png" alt="SuwaPath" width="96" />

# SuwaPath

### Your Health. Our Path.

**An AI patient-navigation, clinical-intake and hospital-intelligence platform for Sri Lanka.**

From a symptom, a lab report or a medical image — to the right verified doctor,
at a facility that can actually run the test you need.

`FastAPI` · `PostgreSQL` · `LangGraph` · `Gemini` · `Qdrant` · `React` · `TypeScript` · `Tailwind`

Built for **AI Buildathon 2026** · Team **Gmora** · SDG 3 — Good Health and Well-Being

</div>

---

## Table of contents

- [What SuwaPath does](#what-suwapath-does)
- [Highlights](#highlights)
- [The core idea](#the-core-idea)
- [Screens](#screens)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Demo accounts](#demo-accounts)
- [Demo walkthroughs](#demo-walkthroughs)
- [Design system](#design-system)
- [Project structure](#project-structure)
- [API overview](#api-overview)
- [Testing and verification](#testing-and-verification)
- [Plugging in your own CV model](#plugging-in-your-own-cv-model)
- [Seeded dataset](#seeded-dataset)
- [Privacy, consent and safety](#privacy-consent-and-safety)
- [What is real, and what is not](#what-is-real-and-what-is-not)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Disclaimer](#disclaimer)

---

## What SuwaPath does

Healthcare access often fails *before* a patient reaches a clinician. People are
unsure whether a symptom is urgent, which specialty is appropriate, which
hospital has the right doctor or scanner, or what their lab report actually
means. They search across disconnected websites and booking services, repeat the
same history at every stage, and frequently give up.

SuwaPath turns an unstructured concern into a structured, trackable care
journey:

```
patient concern → structured intake → red-flag assessment → care level
   → specialty → doctor / hospital / test-facility match → appointment
   → doctor pre-consult summary → consultation → follow-up → care programme
```

The same records serve five roles — **patients**, **guardians**, **doctors**,
**hospital administrators** and **system administrators** — so a booking made by
a patient appears instantly in the doctor's live queue and in the hospital's
demand forecast. It is one workflow, not five dashboards.

---

## Highlights

| | |
|---|---|
| **Multilingual symptom intake** | Conversational triage in **English, Sinhala and Tamil**. Sinhala input fires the exact same clinical rules as English — matching is on concepts, not translated strings. |
| **Deterministic red-flag engine** | 24 clinician-style rules decide urgency. **The LLM never does.** Chest pain + breathlessness + sweating → `EMERGENCY`, every time, in any language. |
| **Capability-aware matching** | The differentiator. Not just "a dermatologist near you", but a dermatologist at a facility that can perform the **skin biopsy** the recommendation calls for. |
| **Medical report understanding** | OCR reads lab PDFs and photographed scans, preserves table row structure, and flags values against **the reference range printed on your own report**. |
| **Medical image screening** | Chest X-ray screening with confidence, an occlusion-sensitivity heatmap, and a hand-off into care navigation. Pluggable adapters for future models. |
| **Care programmes** | Maternal/postpartum with danger-sign check-ins, elderly care with medication adherence and pattern-based guardian alerts, and a confidential sexual-health pathway. |
| **Consent-controlled guardians** | Deny-by-default. A guardian sees only what the patient explicitly granted, and withheld sections are shown as withheld rather than silently hidden. |
| **Hospital intelligence** | No-show risk and 7-day specialty demand forecasting, both fitted on the hospital's own historical appointments. |
| **Works without an API key** | No Gemini key? Every feature still runs on the deterministic engine. Nothing hard-fails in a demo. |

---

## The core idea

### The LLM never decides urgency

This is the load-bearing safety property. Gemini does language work — asking
natural follow-up questions, extracting structure, explaining findings in plain
language. A separate deterministic engine decides the care level, and it
**re-derives symptom concepts from the patient's own words** rather than
trusting the model's symptom list. A hallucinated or omitted symptom therefore
cannot change the care level.

LangGraph makes this a structural property rather than a convention:

```
              ┌─ knowledge  ──────────────► END
              ├─ web_search ──────────────► END
route ────────┼─ handoff (doc / image) ───► END
              └─ symptom_intake
                     │ (enough history?)
                     ├─ no ──────────────► END   (ask another question)
                     └─ yes
                         ▼
              extract ─► red_flag ─► navigate ─► match ─► END
                         ▲
                  deterministic — no model call
```

Nothing can reach `navigate` without traversing `red_flag`. Every symptom
session returns its orchestration trace, so the path taken is visible in the UI.

### Multilingual by concept, not translation

`app/clinical/lexicon.py` maps ~90 clinical concepts to the surface forms
patients actually type — English, Sinhala, Tamil, and romanised transliteration.

```
"I have pain in my chest and shortness of breath"    → EMERGENCY · RF-CARD-001
"මට පපුවේ කැක්කුම සහ හුස්ම ගැනීමේ අපහසුතාව තියෙනවා"    → EMERGENCY · RF-CARD-001
```

A second proximity pass catches phrasing the literal index cannot: *"my eye is
red and painful"* resolves to ophthalmology without the phrase "eye pain" ever
appearing.

### Capability-aware matching

A recommendation carries **required capabilities**, not just a specialty. Ten
ranking factors are scored and returned with a per-factor breakdown, and every
result explains itself:

> *"Recommended because this doctor specialises in dermatology, they have an
> appointment tomorrow, and they practise at a facility providing skin biopsy
> and histopathology."*

Emergency capability overrides ranking preference entirely — an emergency
recommendation only ever surfaces emergency-capable facilities.

---

## Screens

Reference designs live in [`Ui mocks/`](Ui%20mocks). The implemented UI follows
their visual language — teal/cyan brand, navy text, calm card surfaces — with a
lighter information density and role-specific responsive layouts.

| Role | Desktop | Mobile |
|---|---|---|
| **Patient** | Action-first dashboard, symptom chat, provider matching, report and image intelligence | Bottom tab bar, stacked cards |
| **Guardian** | Dependent cards, consent-scoped detail, alert stream | Same, tab bar |
| **Doctor** | Live queue table, pre-consultation summary with original patient answers | Queue rows become readable cards |
| **Hospital admin** | KPI row, demand-vs-capacity charts, no-show risk table | Charts reflow, tables scroll |
| **System admin** | Platform overview, user management, live AI configuration | Responsive |

---

## Architecture

```
   Patient · Guardian · Doctor · Hospital admin · System admin
                            │
              React + Vite + TypeScript + Tailwind (PWA)
                            │  REST · JWT · RBAC
                    FastAPI  —  100 routes
                            │
   ┌────────────────┬───────┴────────┬────────────────┬──────────────┐
   │  LangGraph     │  Deterministic │   Document     │   Vision     │
   │  orchestration │  clinical      │   pipeline     │   adapters   │
   │  (Gemini)      │  engines       │   PyMuPDF +    │   ONNX ⇢     │
   │                │  red-flag ·    │   Tesseract    │   baseline   │
   │                │  navigation ·  │                │              │
   │                │  matching      │                │              │
   └────────────────┴────────────────┴────────────────┴──────────────┘
                            │
        PostgreSQL (41 tables)   ·   Qdrant + MiniLM (knowledge)
```

---

## Tech stack

**Backend** — Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL 16 · PyJWT ·
bcrypt

**AI** — LangGraph (orchestration graph) · Google Gemini (language) ·
Qdrant + `fastembed` MiniLM ONNX (semantic knowledge retrieval) ·
PyMuPDF + Tesseract (OCR) · onnxruntime (computer vision)

**Frontend** — React 19 · TypeScript · Vite · Tailwind CSS 3 · React Router 7 ·
Recharts · Axios

**Analytics** — Logistic regression (no-show) and additive time-series
decomposition (demand), both implemented directly so the coefficients are
inspectable.

---

## Getting started

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12+ | |
| Node.js | 20+ | |
| PostgreSQL | 16 | |
| Tesseract OCR | 5+ | Required for scanned-document OCR |

```bash
brew install postgresql@16 tesseract     # macOS
# sudo apt install postgresql-16 tesseract-ocr   # Debian/Ubuntu
```

### 1 · Start PostgreSQL

```bash
LC_ALL=C pg_ctl -D /opt/homebrew/var/postgresql@16 start
createdb -h 127.0.0.1 -p 5436 suwapath
```

> **Two notes.** `LC_ALL=C` works around a macOS/Homebrew issue where the
> postmaster becomes multithreaded during startup and refuses to boot. Port
> `5436` is what this Homebrew instance is configured to use — check
> `postgresql.conf` and adjust `DATABASE_URL` if yours differs.

Prefer containers? `docker compose up -d` starts PostgreSQL and Qdrant; then
point `DATABASE_URL` and `QDRANT_URL` at them.

### 2 · Backend

> **Stay inside `backend/` for every command below.** The venv lives at
> `backend/.venv`, so running `.venv/bin/python` from the repo root (or any
> other directory) fails with `no such file or directory`. If you `cd` away
> to run something else — `brew install`, `docker compose`, editing `.env` —
> `cd backend` again before the next command here.

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp ../.env.example ../.env          # then add GEMINI_API_KEY

PYTHONPATH=. .venv/bin/python -m app.seed.seeder --reset
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000
```

Seeding takes ~15 seconds and prints the demo accounts when it finishes.

- API: <http://127.0.0.1:8000>
- Interactive docs: <http://127.0.0.1:8000/docs>
- Health + AI status: <http://127.0.0.1:8000/health>

### 3 · Frontend

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>.

### 4 · Verify

```bash
cd backend && PYTHONPATH=. .venv/bin/python tests/test_scenarios.py
```

Runs all seven required demo scenarios plus RBAC boundary checks against the
live API. Expect **73 checks, 0 failures**.

---

## Configuration

All configuration is environment-driven. Copy `.env.example` to `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local socket, port 5436 | PostgreSQL connection |
| `JWT_SECRET` | dev value | **Change before any deployment** |
| `GEMINI_API_KEY` | *(empty)* | Enables the live orchestrator. Without it, deterministic fallbacks run. |
| `GEMINI_MODEL` | `gemini-2.0-flash` | Model id |
| `TAVILY_API_KEY` | *(empty)* | Optional. Current public information only — never clinical decisions. |
| `QDRANT_URL` | *(empty)* | Empty ⇒ embedded on-disk Qdrant in `backend/storage/qdrant` |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Retrieval embeddings |

Check what is actually live at runtime:

```bash
curl -s http://127.0.0.1:8000/health | python3 -m json.tool
```

Or sign in as the system administrator and open **AI Configuration**.

---

## Demo accounts

Password for all accounts: `Demo@1234`

| Email | Person | Role |
|---|---|---|
| `patient@suwapath.lk` | Nimali Fernando | Patient |
| `maternal@suwapath.lk` | Dilini Fernando | Patient — 28 weeks pregnant |
| `elderly@suwapath.lk` | Sunil Fernando | Patient — elderly care, large-text UI |
| `guardian@suwapath.lk` | Nimal Fernando | Guardian of both patients above |
| `doctor@suwapath.lk` | Dr. Dileepa Perera | Doctor — Endocrinology |
| `hospital@suwapath.lk` | Chathurika Bandara | Hospital administrator |
| `admin@suwapath.lk` | Ravindu Wickramasinghe | SuwaPath system administrator |

Confidential sexual-health mode needs no account at all: **`/private`**.

---

## Demo walkthroughs

<details>
<summary><b>A · Symptom navigation → the doctor's queue</b></summary>

1. Sign in as `patient@suwapath.lk` → **Symptom Check**.
2. Type *"I have pain in my chest and I feel dizzy"*. The assistant asks
   follow-up questions rather than jumping to a recommendation.
3. Answer a few; the session completes and shows the structured intake, the
   fired red-flag rules (`RF-CARD-001`), `EMERGENCY` urgency and a cardiology
   recommendation with reason and confidence.
4. **See matching doctors** → results explain themselves and are restricted to
   emergency-capable facilities. Book a slot.
5. Sign in as `doctor@suwapath.lk` → the patient is already in the live queue.
   Open them for the pre-consultation summary — with the original conversation
   preserved alongside the AI-extracted structure.

</details>

<details>
<summary><b>B · Medical report understanding</b></summary>

Sign in as a patient → **Medical Reports** → upload
`backend/storage/samples/cbc_report.pdf` (or `cbc_report_scan.png` to force the
Tesseract path). Extracted rows keep their order, the low haemoglobin is flagged
against the range printed on the report, and a plain-language explanation leads
into provider matching.

</details>

<details>
<summary><b>C · Medical image screening</b></summary>

**Image Screening** → upload `backend/storage/samples/chest_xray_pneumonia.png`.
You get a finding, confidence, a heatmap of the regions that drove the result,
and a route into respiratory medicine. Uploading `not_an_xray.png` is rejected
by modality validation before any inference runs.

</details>

<details>
<summary><b>D · Maternal danger sign → consented guardian alert</b></summary>

Sign in as `maternal@suwapath.lk` → **Care Programmes**. The dashboard shows
pregnancy week 28. Submit a check-in reporting *severe headache* **and**
*blurred vision*: the pre-eclampsia rule fires, escalation appears, and the
guardian is alerted. Sign in as `guardian@suwapath.lk` — the alert is there, but
her **reports remain withheld**, because she never granted that scope.

</details>

<details>
<summary><b>E · Elderly missed medication → guardian books follow-up</b></summary>

`elderly@suwapath.lk` has three consecutive missed doses of a critical
medication. Pattern detection raises a guardian alert (a *run* of misses, not
every single event). As `guardian@suwapath.lk`, open the dependent and book a
follow-up on their behalf.

</details>

<details>
<summary><b>F · Confidential sexual health</b></summary>

Go to `/private` — no account. Answer the structured questions and get
window-period-aware testing guidance, confidential facilities, and a recovery
code. Delete the session; the recovery code stops working immediately.

</details>

<details>
<summary><b>G · Hospital intelligence</b></summary>

Sign in as `hospital@suwapath.lk`. Real KPIs from seeded data, a 7-day forecast,
and a capacity warning computed from actual published schedule capacity
(obstetrics typically ~75 predicted vs 60 capacity).

</details>

---

## Design system

The visual system is centralised so it can be changed in one place — there are
no ad-hoc colours, radii or shadows in page code.

```
frontend/src/styles/
  tokens.css       ← single source of truth: brand ramp, ink ramp, status
                     tones, typography scale, radii, shadows, layout metrics
  components.css   ← every reusable pattern as a named class (.sp-btn,
                     .sp-card, .sp-chip, .sp-table, .sp-tabbar, …)
  theme.ts         ← JS mirror; chart colours read the same tokens at runtime
tailwind.config.js ← Tailwind colours map to the CSS variables
```

**To restyle the product, edit `tokens.css`.** Tailwind utilities, component
classes and Recharts all resolve to the same variables.

The brand ramp is sampled from the actual logo (`#12cddb → #0090b0`, wordmark
navy `#0a2e56`).

Two further conventions:

- **No emoji as UI icons.** All icons are Material Symbols paths inlined in
  `components/Icon.tsx`; they inherit `currentColor`, scale with type, and are
  correctly hidden from screen readers.
- **Accessibility density.** The elderly pathway sets
  `:root[data-density="comfortable"]`, which scales type and control sizing via
  tokens — no component needs a special case.

---

## Project structure

```
SuwaPath/
├── backend/
│   ├── app/
│   │   ├── clinical/        lexicon · red-flag rules · specialty & capability catalogues
│   │   ├── core/            config · database · JWT + RBAC
│   │   ├── models/          41 SQLAlchemy tables
│   │   ├── services/        graph (LangGraph) · red_flag_engine · navigation
│   │   │                    matching · ocr · vision/ · analytics · knowledge
│   │   ├── api/v1/          12 routers
│   │   ├── seed/            deterministic seeder + sample documents/images
│   │   └── knowledge/       curated health corpus
│   ├── storage/samples/     demo lab reports and chest X-ray phantoms
│   └── tests/               end-to-end scenario suite
├── frontend/
│   ├── public/brand/        logo assets, PWA icons
│   └── src/
│       ├── styles/          tokens · components · theme  ← design system
│       ├── components/      AppShell · Icon · ui kit
│       ├── lib/             api client · auth context
│       └── pages/           patient/ guardian/ doctor/ hospital/ admin/
├── models/pneumonia/        drop trained .onnx weights here
├── Ui mocks/                reference designs
├── Logos/                   source brand assets
└── docker-compose.yml       optional PostgreSQL + Qdrant
```

---

## API overview

Full interactive documentation at `/docs`. Principal groups:

| Prefix | Purpose |
|---|---|
| `/api/v1/auth` | Register, login, refresh, profile |
| `/api/v1/symptoms` | Symptom sessions, messages, structured intake, recommendation |
| `/api/v1/providers` | Doctor / hospital / diagnostic-centre matching, slots, catalogues |
| `/api/v1/appointments` | Booking, lifecycle transitions, rescheduling |
| `/api/v1/documents`, `/images` | Upload, OCR, screening, heatmaps |
| `/api/v1/doctor` | Live queue, pre-consultation summary, consultations, referrals |
| `/api/v1/care` | Programmes, check-ins, medications, vitals |
| `/api/v1/guardian`, `/patients/me/guardians` | Dependents, alerts, consent management |
| `/api/v1/hospital` | KPIs, forecast, no-show risk, capacity, roster |
| `/api/v1/admin` | Users, provider verification, facilities, AI config, audit |
| `/api/v1/confidential` | Anonymous sexual-health sessions |

---

## Testing and verification

```bash
cd backend && PYTHONPATH=. .venv/bin/python tests/test_scenarios.py
```

The suite drives the live API through all seven required scenarios end to end
and asserts the RBAC boundaries:

- a doctor cannot open an unrelated patient's record
- a hospital admin cannot reach the clinical queue
- a patient cannot reach hospital analytics
- a guardian cannot read a non-dependent
- unauthenticated requests are rejected

**Current status: 73 checks passing, 0 failing.**

---

## Plugging in your own CV model

The adapter architecture, modality validation, heatmaps and the navigation
hand-off are complete. Only the trained weights are pending.

Drop your export in and restart:

```
models/pneumonia/model.onnx
```

`OnnxPneumoniaAdapter` picks it up automatically and takes priority over the
bundled baseline. The loader reads input shape, channel count and NCHW/NHWC
layout from the graph, so Keras and PyTorch exports both work. Single-logit
sigmoid heads, 2-class softmax heads and pre-normalised probability vectors are
all handled. Class order is assumed `[normal, pneumonia]`.

```python
# PyTorch
torch.onnx.export(model, torch.randn(1, 3, 224, 224), "model.onnx",
                  input_names=["input"], output_names=["output"],
                  dynamic_axes={"input": {0: "batch"}})
```

```bash
# Keras
pip install tf2onnx
python -m tf2onnx.convert --saved-model saved_model_dir --output model.onnx
```

Confirm at **Admin → AI Configuration**, or `GET /api/v1/vision/adapters`.

Heatmaps use **occlusion sensitivity** rather than Grad-CAM, deliberately: it is
model-agnostic, so a dropped-in ONNX graph gets a visual explanation without
needing named convolutional layers.

---

## Seeded dataset

Fictional, Sri Lankan, deterministic (fixed RNG seed). **No real doctor or
hospital is named or implied.**

| | |
|---|---|
| Patients | 3,000 (300 fully-profiled app users + wider hospital patient base) |
| Doctors | 70 across 16 specialties |
| Hospitals / diagnostic centres | 16 / 10 |
| Guardian relationships | 120, with varied per-scope consent |
| Appointments | ~69,000 across a 141-day window |
| Consultations / referrals | 300 / ~60 |
| Care enrolments | Maternal, postpartum and elderly cohorts |

**Why 3,000 patients rather than the specified 300.** Appointments are generated
by walking each doctor's *real published schedule*, so 70 doctors at realistic
clinic utilisation produce tens of thousands of visits. Attributing those to 300
people would give every patient hundreds of appointments a year and make the
no-show model meaningless. The 300 fully-profiled app users are still there.

This is also what makes the analytics defensible: the seeded no-show rate lands
at ~12–15% (matching real outpatient clinics), and specialty utilisation is
uneven **by design** — obstetrics and cardiology run over capacity while others
sit at 50–70%, so the capacity warning fires from real data rather than a
hard-coded flag.

---

## Privacy, consent and safety

- **Guardian access is deny-by-default.** A relationship grants nothing; each
  scope is an explicit row the patient controls. Withheld sections are shown to
  the guardian *as withheld*, not silently hidden.
- **Doctors cannot browse.** Record access requires an appointment or referral
  linking that doctor to that patient.
- **Hospital administrators see operations**, never clinical conversations.
- **Confidential sessions are structurally separate.** The table has no foreign
  key to `users` and stores only a hash of the recovery code. Deletion clears
  the content, not just a flag.
- **AI summaries never replace patient words.** Raw conversation turns and
  AI-extracted structure live in separate tables; the pre-consultation view
  shows both.
- **Every AI output carries** a recommendation, a reason, a confidence and a
  suggested next action.

---

## What is real, and what is not

An honest inventory, because "works end to end" should mean something.

| Component | Status |
|---|---|
| Red-flag / urgency engine | **Real** — 24 deterministic rules, context-aware (pregnancy, age, chronic conditions) |
| Care navigation + explanations | **Real** — weighted concept→specialty scoring, confidence from score margin |
| Provider / facility matching | **Real** — haversine distance, live slot availability, capability set intersection |
| Appointment lifecycle | **Real** — all 8 states with enforced transition guards |
| OCR document pipeline | **Real** — PyMuPDF text layer, Tesseract fallback, column-aware table parsing, report-printed reference ranges |
| No-show prediction | **Real** — logistic regression fitted on seeded history; recovers the generative signal |
| Demand forecasting | **Real** — level + trend + weekday seasonality vs actual schedule capacity |
| Knowledge retrieval | **Real** — Qdrant + MiniLM ONNX embeddings over a 30-document curated corpus |
| Gemini orchestration | **Real when `GEMINI_API_KEY` is set** — deterministic fallback otherwise |
| **Pneumonia CV model** | **Baseline placeholder** — see below |

### The computer-vision model

`BaselinePneumoniaAdapter` is a transparent, **untrained** heuristic. It computes
genuine radiographic features from the pixels — lower-zone opacity relative to
upper zones (excluding the diaphragm), left/right asymmetry, and local texture
heterogeneity — rather than returning a random number. It reports
`is_trained_model: false`, and both the API and the admin console label it as a
baseline. See [Plugging in your own CV model](#plugging-in-your-own-cv-model).

The sample chest images in `backend/storage/samples/` are **procedurally
generated phantoms**, not real radiographs. Replace them with de-identified
clinical images when evaluating an actual model.

---

## Troubleshooting

<details>
<summary><b>zsh: no such file or directory: .venv/bin/python</b></summary>

You ran the command from the wrong directory. The virtual environment lives at
`backend/.venv`, not at the repo root — this happens after `cd backend`,
creating the venv, then `cd ..` for an unrelated step (installing Homebrew
packages, editing `.env`) without `cd backend` again before continuing. Run
`cd backend` and retry.
</details>

<details>
<summary><b>Login fails in the browser but works with curl</b></summary>

The frontend defaults to `http://127.0.0.1:8000` (see `VITE_API_BASE` in
`frontend/src/lib/api.ts`). If the backend was restarted, crashed, or is still
seeding, requests from the browser fail even though a direct `curl` to a
previously-open terminal succeeds. Confirm `curl http://127.0.0.1:8000/health`
returns `"status":"ok"` first, then retry in the browser.
</details>

<details>
<summary><b>PostgreSQL won't start: "postmaster became multithreaded during startup"</b></summary>

A macOS/Homebrew locale issue. Start it with `LC_ALL=C`:

```bash
LC_ALL=C pg_ctl -D /opt/homebrew/var/postgresql@16 start
```
</details>

<details>
<summary><b>Database connection refused</b></summary>

This Homebrew instance listens on **5436**, not the default 5432. Check
`grep port /opt/homebrew/var/postgresql@16/postgresql.conf` and set
`DATABASE_URL` accordingly.
</details>

<details>
<summary><b>OCR returns no values</b></summary>

Confirm Tesseract is installed and on `PATH` (`tesseract --version`). Text-layer
PDFs do not need it; scanned images do.
</details>

<details>
<summary><b>"Gemini: fallback" in /health</b></summary>

Expected when `GEMINI_API_KEY` is unset. Everything still works — conversation
wording is scripted rather than generated. Add the key and restart.
</details>

<details>
<summary><b>Knowledge retrieval falls back to TF-IDF</b></summary>

The MiniLM ONNX model downloads on first use (~90 MB). Without network access
the service degrades to an in-process TF-IDF index; retrieval still works, with
lower semantic quality.
</details>

<details>
<summary><b>Frontend shows 401s after re-seeding</b></summary>

`--reset` recreates users with new ids, invalidating existing tokens. Sign in
again.
</details>

---

## Roadmap

Known limitations, stated plainly:

- Teleconsultation links are placeholders; no video service is integrated.
- Notifications are in-app only — no push, SMS or email transport.
- Sinhala/Tamil coverage is strong for clinical concepts and fallback strings;
  full UI localisation is English-only so far.
- The frontend ships as a single JS chunk; it should be code-split per role
  before production.
- No automated frontend tests — verification is the backend scenario suite plus
  manual UI review.
- Voice input (spec'd in the problem statement) is not yet implemented.

---

## Disclaimer

SuwaPath provides **care navigation and screening support**. It is **not a
diagnostic device** and does not replace professional medical judgement. All
recommendations are presented with reasoning, confidence and a suggested next
action, and are intended to be confirmed by a qualified healthcare
professional.

All patients, doctors, hospitals and diagnostic centres in the seeded dataset
are **fictional**. No affiliation with any real Sri Lankan healthcare
institution is claimed or implied.

<div align="center">

**SuwaPath** · Team Gmora · AI Buildathon 2026

</div>
