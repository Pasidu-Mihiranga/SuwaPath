# SuwaPath — Roles, Features and Technical Reference

**Your Health. Our Path.**

An AI patient-navigation, clinical-intake and hospital-intelligence platform for
Sri Lanka. From a symptom, a lab report or a medical image — to the right
verified doctor, at a facility that can actually run the test you need.

> Built for AI Buildathon 2026 · Team Gmora · SDG 3 — Good Health and Well-Being

This document explains **who uses the system, what each role can do, how every
feature actually works, and what the whole thing is built on.** It is derived
from the code (112 API endpoints, 100+ backend modules, 27 frontend routes) and
the project's own docs.

---

## Table of contents

- [The problem and the core idea](#the-problem-and-the-core-idea)
- [The five roles at a glance](#the-five-roles-at-a-glance)
- [Role 1 — Patient](#role-1--patient)
- [Role 2 — Guardian](#role-2--guardian)
- [Role 3 — Doctor](#role-3--doctor)
- [Role 4 — Hospital Administrator](#role-4--hospital-administrator)
- [Role 5 — System Administrator](#role-5--system-administrator)
- [Anonymous — Confidential Mode](#anonymous--confidential-mode-no-account)
- [How access control is enforced](#how-access-control-is-enforced)
- [The AI subsystem](#the-ai-subsystem)
- [The clinical safety engine](#the-clinical-safety-engine)
- [Document and image intelligence](#document-and-image-intelligence)
- [Hospital analytics and ML](#hospital-analytics-and-ml)
- [The autonomy layer](#the-autonomy-layer)
- [Privacy, consent and encryption](#privacy-consent-and-encryption)
- [Tech stack](#tech-stack)
- [Architecture](#architecture)
- [API surface](#api-surface)
- [What is real vs placeholder](#what-is-real-vs-placeholder)

---

## The problem and the core idea

Healthcare access often fails *before* a patient reaches a clinician. People are
unsure whether a symptom is urgent, which specialty is appropriate, which
hospital has the right doctor or scanner, and what their lab report means. They
search across disconnected websites, repeat the same history at every stage, and
frequently give up.

SuwaPath's differentiator is **capability-aware matching**. Most directories
answer "which doctors have this specialty near me?" SuwaPath answers a harder
question: *which providers work at a facility that can actually deliver the
diagnostic step this recommendation calls for?* A recommendation for an
echocardiogram only returns cardiologists at facilities whose capability set
includes echocardiography.

Three principles run through the codebase:

1. **Urgency is never decided by a language model.** A deterministic rule engine
   with 24 clinical rules decides the care level. The LLM only extracts
   structure from conversation; the engine ranks above every downstream score.
2. **Every recommendation explains itself.** Matching returns a per-factor score
   breakdown and a human-readable reason.
3. **The platform degrades, never breaks.** With no LLM API key at all, every
   flow still runs end to end on deterministic composers — the wording is
   plainer, nothing else changes.

---

## The five roles at a glance

| Role | Enum value | Home route | Purpose |
|---|---|---|---|
| **Patient** | `patient` | `/patient` | Understand symptoms, find the right care, manage records and ongoing treatment |
| **Guardian** | `guardian` | `/guardian` | Watch over a dependent's health within consent limits the patient sets |
| **Doctor** | `doctor` | `/doctor` | Run a clinical day: queue, consultations, patient records, referrals |
| **Hospital Admin** | `hospital_admin` | `/hospital` | Operational intelligence for one facility — demand, no-shows, capacity |
| **System Admin** | `system_admin` | `/admin` | Platform governance — users, provider verification, AI config, audit |

Roles are defined in `backend/app/models/enums.py` (`UserRole`) and enforced on
both sides: React route guards in `frontend/src/App.tsx` (`<Protected roles={…}>`)
and FastAPI dependencies in `backend/app/api/deps.py`.

---

## Role 1 — Patient

**Purpose:** get from "something feels wrong" or "I have a report I don't
understand" to a booked appointment with the right verified doctor — without
knowing any medical vocabulary.

**Demo accounts:** `patient@suwapath.lk`, `maternal@suwapath.lk` (28 weeks
pregnant), `elderly@suwapath.lk` — password `Demo@1234`

### Screens and capabilities

| Screen | Route | What the patient can do |
|---|---|---|
| Dashboard | `/patient` | Current care recommendation, upcoming appointments, medication doses due, quick actions |
| AI Assistant | `/patient/assistant` | One conversation that takes a history, reads a report, and books an appointment |
| Doctors & Hospitals | `/patient/find-care` | Search and filter verified providers, see why each was matched, view live slots, book |
| Appointments | `/patient/appointments` | Book, reschedule, cancel, check in; full 8-state lifecycle |
| Medical Reports | `/patient/reports` | Upload lab reports and prescriptions; get analyte-level explanations |
| Image Screening | `/patient/imaging` | Upload a chest X-ray for screening support with a heatmap overlay |
| Care Programmes | `/patient/programmes` | Maternal, postpartum, elderly and chronic care; daily check-ins, vitals, medication logging |
| Medical History | `/patient/history` | Consolidated timeline of consultations, documents, results |
| Sharing & Consent | `/patient/sharing` | Grant, scope and revoke guardian access — per permission type |
| Notifications | `/patient/notifications` | Appointment, medication, report and follow-up alerts |
| Profile / Settings | `/patient/profile`, `/settings` | Demographics, chronic conditions, allergies, language, location |

### Key patient features explained

**The AI Assistant** (`/api/v1/agent/chat`, `/agent/stream`)
Deliberately unified — the old separate symptom checker now redirects here.
One conversation can take a history, read an uploaded report and book an
appointment, rather than three that cannot see each other. It conducts
**doctor-style history taking** (onset, duration, severity, associated
symptoms), streams responses, keeps durable memory across sessions, and shows
which LLM provider answered and how fast.

**Symptom intake → care navigation**
The assistant extracts clinical concepts, the deterministic red-flag engine
assigns an urgency level (`emergency` / `urgent` / `routine` / `self_care`), and
the navigation engine maps concepts to a specialty using weighted scoring, with
confidence derived from the score margin. Emergencies get an immediate
escalation message naming Suwa Seriya ambulance (1990).

**Capability-aware provider matching** (`backend/app/services/matching.py`)
Ranked on ten weighted factors — specialty (30), sub-specialty (8), availability
(18), distance (14, haversine from patient coordinates), plus verification
status, facility capability intersection, urgency fit and more. Every result
carries an explanation of why it ranked where it did.

**Medical report intelligence** (READ → STRUCTURE → EXPLAIN → NAVIGATE)
PDFs use their embedded text layer when present; scanned pages fall back to
Tesseract OCR. Lines are parsed into analyte rows preserving table order with
units and reference ranges. Values are flagged against **the reference range
printed on the report itself**, falling back to a catalogue range only when the
report carries none. Explanations are grounded in the knowledge corpus, and the
result feeds the same care-navigation engine.

**Care programmes**
Four programme types with real longitudinal tracking: maternal (pregnancy week,
check-ins), postpartum, elderly, and chronic. Patients log medication doses
(`taken` / `skipped` / `snoozed` / `missed`), record vitals, and submit daily
wellbeing check-ins (`good` / `not_great` / `need_help`).

**Consent management**
The patient is the sole authority over guardian access. Eight permission scopes
exist — appointments, medications, reminders, wellbeing, care programme,
emergency alerts, reports, full medical. **Default is deny-all.** Access can be
revoked at any time.

---

## Role 2 — Guardian

**Purpose:** let a family member support a patient's care — an elderly parent, a
pregnant relative, a child — *strictly within the boundaries the patient sets.*

**Demo account:** `guardian@suwapath.lk` (guardian of both the maternal and
elderly demo patients)

| Screen | Route | Capability |
|---|---|---|
| My Dependents | `/guardian` | List of patients who have granted access, each with a risk summary |
| Dependent Detail | `/guardian/dependents/:patientId` | Scoped view of one dependent — only the sections consented to |
| Alerts | `/guardian/alerts` | Missed doses, missed check-ins, wellbeing escalations; acknowledgeable |
| Notifications | `/guardian/notifications` | Inbox |

### How the consent boundary actually works

This is the most carefully guarded part of the system. A guardian never gets a
patient record — they get **exactly the scopes the patient granted**, checked on
every request:

- `get_relationship()` confirms an active guardian relationship exists, else 403.
- `require_permission()` enforces the specific scope for that endpoint.
  `FULL_MEDICAL` implies every other scope; nothing else does.
- The 403 message is deliberately helpful rather than opaque: *"The patient has
  not granted access to 'medications'. They can enable this in their sharing
  settings."*

A guardian attempting to reach a non-dependent is rejected — this is one of the
RBAC boundary cases covered by the test suite.

**Dependent risk scoring** (`services/dependent_risk.py`) summarises how a
dependent is doing — adherence trend, check-in gaps, upcoming appointments —
without exposing clinical detail the guardian has no scope for.

---

## Role 3 — Doctor

**Purpose:** remove the repeated-history problem. A doctor opens a patient and
the intake conversation, uploaded reports and extracted history are already
there.

**Demo account:** `doctor@suwapath.lk` (Dr. Dileepa Perera, endocrinologist)

| Screen | Route | Capability |
|---|---|---|
| Dashboard | `/doctor` | Today's load, queue status, pending referrals |
| Patient Queue | `/doctor/queue` | Live queue — who has checked in, who is waiting, who is in consultation |
| Appointments | `/doctor/appointments` | Schedule across days, status transitions |
| My Patients | `/doctor/patients` | Only patients with a genuine care relationship |
| Patient Detail | `/doctor/patients/:patientId` | Full record + AI pre-consultation summary |

### Key doctor features

**Pre-consultation summary** (`GET /doctor/patients/{id}/pre-consultation`)
The headline feature. Before the patient sits down, the doctor sees a structured
brief: presenting complaint, history as taken by the assistant, extracted report
values that are out of range, current medications, allergies, chronic
conditions, and the red-flag engine's urgency assessment with the rules that
fired.

**Consultation lifecycle**
`POST /doctor/consultations` starts one, `PATCH` updates notes and findings,
`POST …/complete` closes it. Statuses: `scheduled` → `in_progress` →
`completed` / `cancelled`.

**Referrals** (`POST /doctor/referrals`)
A doctor refers to another specialty or facility. Referrals carry status
(`pending`, `accepted`, `completed`, `declined`, `expired`) and — importantly —
a referral that is never booked is picked up by an autonomous detector and
surfaced rather than silently expiring.

### The doctor access rule

A doctor sees **only patients booked with them or referred to them.** This is
enforced centrally in `resolve_patient_access()`, not per-endpoint, so it cannot
be forgotten on a new route. "Doctor blocked from an unrelated patient record"
is an explicit test case.

---

## Role 4 — Hospital Administrator

**Purpose:** turn the platform's appointment history into operational decisions
for one facility — staffing, capacity, and reducing wasted slots.

**Demo account:** `hospital@suwapath.lk` (Chathurika Bandara)

| Screen | Route | Capability |
|---|---|---|
| Dashboard | `/hospital` | KPIs computed from real seeded data — volume, utilisation, no-show rate |
| Demand Forecast | `/hospital/forecast` | 7-day per-specialty forecast with prediction intervals |
| No-show Risk | `/hospital/no-show` | Per-appointment risk with contributing factors |
| Capacity | `/hospital/capacity` | Forecast vs actual schedule capacity, high-demand warnings |
| Providers | `/hospital/providers` | Doctors at this facility |

### The two models (both genuinely fitted, not hard-coded)

**No-show prediction** — logistic regression trained by gradient descent on the
hospital's own completed/no-show history. Coefficients are exposed in the UI so
staff see *why* an appointment is high risk: *"History of missed appointments ·
Appointment never confirmed · Monday appointment · Booked far in advance."*

**Demand forecasting** — additive decomposition: level + trend + day-of-week
seasonality per specialty, with a residual-based prediction interval, compared
against real schedule capacity to raise capacity warnings.

**Model quality** (`GET /hospital/model-quality`) — AUC, PR-AUC, Brier score,
ECE and a calibration table, computed in pure numpy on a held-out time split. An
unusual and honest touch: the platform reports how good its own model is.

### The facility scope rule

A hospital admin is **scoped to their own facility** — enforced in `deps.py`, not
trusted from a request parameter. They also cannot reach the clinical queue;
operational data and clinical data are separate surfaces. Both are tested RBAC
boundaries.

---

## Role 5 — System Administrator

**Purpose:** govern the platform itself — who is on it, which providers are
genuine, how the AI behaves, and what happened.

**Demo account:** `admin@suwapath.lk` (Ravindu Wickramasinghe)

| Screen | Route | Capability |
|---|---|---|
| Overview | `/admin` | Platform-wide metrics |
| Users & Roles | `/admin/users` | List, create staff users, activate/deactivate |
| Provider Verification | `/admin/providers` | Approve or reject doctors — `verified` / `pending` / `rejected` |
| Facilities | `/admin/facilities` | Hospitals and diagnostic centres; toggle capabilities |
| AI Configuration | `/admin/ai` | Provider status, model selection, runtime config |
| Audit Log | `/admin/audit` | Every consequential action, including PHI boundary crossings |

**Provider verification matters functionally, not just administratively.**
Verification status is a ranking factor in provider matching — an unverified
doctor does not surface the same way. Toggling a facility's capabilities
directly changes which recommendations can route there.

The system admin also has the broadest data reach (`resolve_patient_access()`
grants access to any patient record), which is precisely why the audit log
exists.

---

## Anonymous — Confidential Mode (no account)

**Route:** `/private` · **API:** `/api/v1/confidential/*`

A deliberately separate surface for sexual-health questions, built on the
observation that the people who most need this information are the least likely
to create an account.

- **No account, no login.** A session is identified by an opaque id plus a
  recovery code whose **hash alone** is stored.
- **Nothing joins to `users`.** An anonymous session can never be correlated
  with a normal patient record — this is a schema-level guarantee, not a policy.
- The user can **delete the session outright.**
- It still does real work: guided questions, then confidential facility matching
  for testing and treatment.

There is also **private mode** inside the normal assistant: chats encrypted
under a PBKDF2 key derived from the user's own PIN, absent from history, with a
12-hour expiry. The server cannot read them.

---

## How access control is enforced

Three rules live in one place — `backend/app/api/deps.py` — rather than being
repeated across 112 endpoints:

```
resolve_patient_access(db, current_user, patient_user_id, permission=…)
├── self             → always allowed
├── system_admin     → any record (audited)
├── guardian         → active relationship + the specific consent scope
├── doctor           → only patients booked with or referred to them
└── otherwise        → 403
```

Hospital admins are separately scoped to their own facility. The frontend
mirrors this with `<Protected roles={…}>` route guards, but the frontend guard
is convenience — **the API is the boundary.**

`tests/test_scenarios.py` runs 73 checks against the live API, including five
explicit RBAC boundary cases (doctor→unrelated patient, admin→clinical queue,
patient→hospital analytics, unauthenticated, guardian→non-dependent).

---

## The AI subsystem

### Multi-provider LLM router with failover

Three providers tried in order, each independently allowed to fail:

1. **Groq** — fastest free tier (~150–400 ms)
2. **OpenRouter** — free model slugs, slower, more rate-limited
3. **Gemini** — free keys are frequently provisioned with `limit: 0`
4. **None** — every caller has a deterministic composer to fall back on

A failed provider gets a 60-second cooldown rather than being retried on every
request. Every completion reports which provider answered and how long it took,
surfaced in the UI. **A missing model degrades wording, never safety.**

### LangGraph agent — supervisor with parallel fan-out

```
guard_input ─(blocked)────────────────────────────────► END
     │
     ▼
   route ──► supervisor ─┬─► clinical_agent ─┐
                         ├─► admin_agent    ─┤
                         ├─► records_agent  ─┼─► merge ─► judge ─► END
                         ├─► knowledge_agent─┤
                         └─► direct_agent   ─┘
```

The supervisor dispatches to one *or more* agents in the same superstep. "Is my
appointment still on, and what did my blood test mean?" runs the admin and
records agents **concurrently**. `guard_input` and `judge` are rule-based, and
**clinical urgency is never produced by this graph** — it is read from the
red-flag engine.

### Cache-Augmented Generation (CAG)

Two cache layers sit in front of the graph:

- **Layer 1 — canned.** Greetings, thanks and product FAQs matched by normalised
  text and embedding similarity, returned verbatim. For a medical product this
  is a *safety* feature as much as a speed one: the answer to "are my messages
  private?" must be identical every time and must never be improvised.
- **Layer 2 — personal.** Per-user, per-session memory of facts already given —
  which stops the assistant asking "how old are you?" three turns running.

### Durable memory

Extraction runs *after* a turn is answered, so it adds no latency. It is
deliberately conservative: a fact is recorded only when the patient stated it
about themselves, and the extractor gets a **closed list of keys** rather than
being allowed to invent them. Deterministic regex patterns run first and are
trusted over the model — *"I'm allergic to penicillin"* is a regex, not a
reasoning problem.

### Knowledge retrieval (RAG)

Qdrant + MiniLM ONNX embeddings via `fastembed` (no torch). Falls back to an
in-process TF-IDF cosine index when the model can't be downloaded. **Three
separate collections**, deliberately not one pool:

| Collection | Contents |
|---|---|
| `clinical_knowledge` | General patient guidance, never patient-specific |
| `provider_directory` | Doctors, hospitals, tests — **generated from the database**, never hand-written |
| `policy_faq` | How SuwaPath itself works: consent, private mode, AI limits |

Mixing them would let *"which doctor treats asthma?"* retrieve an article
*about* asthma and answer with no doctor in it. **Patient records are never
indexed in any collection.**

### Agent actions — the T0/T1/T2 risk ladder

Every tool in `tools.py` is a *read*. Actions are the other half — things that
change something — kept in a separate registry because reads and writes need
different rules.

| Tier | Behaviour | Rationale |
|---|---|---|
| **T0** | Executes immediately (still audited) | Reversible and cheap to get wrong. Asking permission to set a reminder trains people to tap Approve without reading — worse than not asking. |
| **T1** | Fully prepared, executed on one tap | The system picks the doctor, slot and fee; the human supplies only the decision. |
| **T2** | Never automated | Not "requires approval" — *absent from the registry entirely.* |

Proposals are **re-authorised at execution time**, not just at creation.

---

## The clinical safety engine

`backend/app/services/red_flag_engine.py` — intentionally *not* AI.

- **24 deterministic rules**, context-aware for pregnancy, age and chronic
  conditions.
- Produces one of four levels: `emergency`, `urgent`, `routine`, `self_care`.
- **Its output outranks every downstream ranking score.**
- Emergency escalation names the real service: *"Go to the nearest emergency
  department or call 1990 (Suwa Seriya ambulance). Do not drive yourself."*

The division of labour is the important part: **the LLM extracts structure from
the conversation; the rule engine decides the care level from that structure.**
A model that hallucinates cannot make something less urgent than it is.

Guardrails run on both sides of the graph — `guard_input` before, `judge` after,
both rule-based, with one judge rewrite permitted.

---

## Document and image intelligence

### OCR pipeline

| Stage | Implementation |
|---|---|
| **Read** | PyMuPDF embedded text layer (fast, exact); Tesseract OCR fallback for scans |
| **Structure** | Column-aware table parsing into analyte rows, preserving order, with units and ranges |
| **Explain** | Flagged `low` / `normal` / `high` / `critical` / `unknown` against the report's own printed range |
| **Navigate** | Feeds the shared care-navigation engine |

Document types: lab report, prescription, radiology report, discharge summary,
clinical report, other.

### Computer vision — chest X-ray pneumonia screening

Two adapters behind one interface:

- **`OnnxPneumoniaAdapter`** — the real path. Drop any `.onnx` file into
  `models/pneumonia/` and it is picked up on the next request. Input shape,
  channel count and layout are read from the graph, so Keras (NHWC) and PyTorch
  (NCHW) exports both load **without code changes**.
- **`BaselinePneumoniaAdapter`** — a transparent, **untrained** fallback so the
  flow is demonstrable before weights exist. It computes genuine radiographic
  features from pixels (lower-zone opacity vs upper zones, left/right asymmetry,
  texture heterogeneity) rather than returning a random number. It reports
  `is_trained_model = False`, and the API and UI label it as **screening support
  only, not a diagnosis** — visible in the patient dashboard screenshot.

A heatmap overlay is served at `GET /images/{id}/heatmap`.

---

## Hospital analytics and ML

Both models are **fitted from the hospital's own historical appointments**, so
dashboard numbers move when the data moves.

| Model | Method | Output |
|---|---|---|
| No-show | Logistic regression, gradient descent | Per-appointment risk band + contributing factors |
| Demand | Level + trend + weekday seasonality per specialty | 7-day forecast + prediction interval |
| Evaluation | Pure numpy, held-out time split | AUC, PR-AUC, Brier, ECE, calibration table |

This is also what makes the analytics defensible: the seeded no-show rate is
generated from a known signal, and the fitted model **recovers that signal**.

---

## The autonomy layer

Eight detectors run on a schedule inside the API process (APScheduler, threads
not asyncio — every service here uses synchronous SQLAlchemy).

| Detector | What it finds |
|---|---|
| `appointments` | Elapsed appointments to mark no-show, offers a rebook |
| `checkins` | Missed daily check-ins → alerts guardians |
| `medication` | Overdue doses, alerts on repeated misses |
| `followups` | Follow-ups a doctor asked for that were never booked |
| `referrals` | Recommended care that was never booked |
| `disengagement` | Patients gone quiet or whose adherence is sliding |
| `noshow` | Proposes reminder batches for appointments predicted to be missed |
| `directory` | Proposes a directory rebuild when providers have changed |

Plus a task drain every 60 seconds.

**Design decisions worth noting:** the schedule is *code*, not database rows — a
schedule in a table drifts away from the handlers it names. Durability lives in
`agent_tasks` instead: the *work* survives restarts, the *timetable* is rebuilt
from the file every boot. Jobs claim work with `SKIP LOCKED`, dedupe by intent,
and run under a Postgres advisory lock so a second process is safe rather than
duplicated. A detector that throws is caught so one bad record cannot stop all
autonomy.

**Acknowledged limitation** (stated in the code, not hidden): the scheduler
lives inside the API process and dies with it. Acceptable for single-machine
deployment; the same registry can be run as a separate worker process.

---

## Privacy, consent and encryption

### The PHI boundary

`backend/app/privacy/boundary.py` — what may cross from SuwaPath into a
third-party LLM. Its opening argument is that **masking alone is not a privacy
architecture**: redacting a name either destroys the utility the model needs or
leaks anyway through quasi-identifiers — *"a 34-year-old pregnant woman in
Nugegoda with a rare condition is identifiable without her name ever appearing."*

So masking is the **last** layer, not the first. Five controls, in order of how
much they actually protect:

1. **Minimisation** — the agent never receives a record, only the smallest field
   set a route needs (`ROUTE_FIELDS`).
2. **Local-only paths** — whole capabilities never call an LLM at all
   (`LOCAL_ONLY_CAPABILITIES`).
3. **Pseudonymisation** — direct identifiers become stable opaque tokens before
   egress and are rehydrated on return, so the model reasons about `PERSON_A7`
   without ever holding an identity.
4. **Egress guard** — a pre-flight scan blocks the call outright if an
   unexpected identifier survived.
5. **Audit** — every crossing recorded: route, data classes, byte count, blocked
   or not.

Data is classified as `direct_identifier`, `quasi_identifier`, `clinical`,
`operational` or `non_sensitive`.

A deliberate non-goal, documented rather than hidden: this does not claim to
make a free-tier hosted LLM safe for real patient data. That is a deployment
decision.

### Encryption at rest

AES-256-GCM over conversation content, at the **application** layer — because
disk encryption protects a stolen server but does nothing about the cases that
actually happen: a leaked backup, a snapshot copied to a laptop, a read replica
handed to an analyst, or SQL injection returning rows. In all of those the
database hands over plaintext happily. Encrypting the column means **the
ciphertext is what leaks.**

GCM authenticates as well as encrypts — a modified ciphertext fails to decrypt
rather than yielding altered text. For medical content, a silently corrupted
"no known allergies" is worse than an error. Ciphertext is versioned; retention
is 90 days.

Two key types: the **server key** (`SUWAPATH_ENCRYPTION_KEY`) for ordinary
conversations, and a **PBKDF2 key derived from the user's PIN** for private mode
— which the server cannot derive on its own.

---

## Tech stack

### Backend

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| Web framework | FastAPI 0.115.6 + Uvicorn 0.34.0 |
| ORM / DB | SQLAlchemy 2.0.36 · PostgreSQL 16+ · psycopg2 |
| Validation | Pydantic 2.10.4 + pydantic-settings |
| Auth | PyJWT 2.10.1 (access + refresh) · bcrypt 4.2.1 |
| Crypto | `cryptography` — AES-256-GCM, PBKDF2 |
| AI orchestration | LangGraph 0.2.60 |
| LLM providers | Groq · OpenRouter · Google Gemini (`google-genai`) |
| Vector store | Qdrant 1.12.1 — **embedded on-disk by default, no server** |
| Embeddings | `fastembed` 0.4.2 → MiniLM ONNX (no torch) |
| OCR | PyMuPDF 1.25.1 + pytesseract 0.3.13 |
| CV / numerics | onnxruntime · numpy 2.2.1 · Pillow |
| Scheduler | APScheduler 3.11.2, in-process, no broker |
| Web search | Tavily (optional) |
| HTTP client | httpx 0.28.1 |

### Frontend

| Layer | Technology |
|---|---|
| Framework | React 19.2 + TypeScript ~6.0 |
| Build | Vite 8.2 |
| Routing | react-router-dom 7.18 |
| Styling | Tailwind CSS 3.4 + PostCSS, custom design tokens |
| Charts | Recharts 3.10 |
| HTTP | axios 1.19 |
| Linting | oxlint 1.75 |
| Mobile | PWA-oriented shell — curved 5-section bottom navbar, quick-action sheet, fixed header, floating chat FAB |

### Infrastructure

| Concern | Choice |
|---|---|
| Local development | **No Docker required** — Homebrew PostgreSQL + embedded Qdrant |
| Containers | Optional `docker-compose.yml` for Postgres + Qdrant |
| Deployment | VPS via Docker Compose behind system nginx + certbot |
| CI/CD | GitHub Actions on push to `main` |
| Ports | API `8000` · Vite dev `5173` · Postgres `5432`/`5436` |

**Notable non-choices, all deliberate:** no Celery (needs a broker, no Redis
here); no torch (fastembed runs MiniLM through ONNX); no Docker for local dev;
no database-stored job schedule.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  React 19 + TypeScript + Vite  (5 role-gated SPAs)       │
│  AppShell · route guards · PWA mobile shell              │
└────────────────────────┬─────────────────────────────────┘
                         │ axios / JWT
┌────────────────────────▼─────────────────────────────────┐
│  FastAPI  —  15 routers, 112 endpoints                   │
│  deps.py: RBAC + guardian consent + facility scope       │
├──────────────────────────────────────────────────────────┤
│  Services                                                │
│  ├─ red_flag_engine   deterministic, 24 rules            │
│  ├─ navigation        concept → specialty                │
│  ├─ matching          capability-aware, 10 factors       │
│  ├─ booking/availability/timeslots                       │
│  ├─ analytics         no-show LR + demand forecast       │
│  ├─ ocr / vision      PyMuPDF+Tesseract / ONNX           │
│  ├─ knowledge         Qdrant + MiniLM, 3 collections     │
│  ├─ memory · chat_store · alerts · delivery              │
│  └─ jobs              8 detectors, APScheduler           │
├──────────────────────────────────────────────────────────┤
│  Agent (LangGraph)   guard → route → supervisor          │
│                      → fan-out → merge → judge           │
│  privacy/boundary.py  minimise → pseudonymise → guard    │
├──────────────────────────────────────────────────────────┤
│  PostgreSQL (SQLAlchemy)   ·   Qdrant (embedded on-disk) │
│  AES-256-GCM at the column level                         │
└──────────────────────────────────────────────────────────┘
```

### Backend module map

```
backend/app/
├── main.py           FastAPI app, lifespan, CORS, retrieval warm-up
├── api/
│   ├── deps.py       RBAC — the three access rules, centralised
│   └── v1/           15 routers (auth, agent, care, clinical, hospital, …)
├── agent/            graph · supervisor · tools · actions · cag · react
│                     guardrails · websearch · state · consult
├── clinical/         catalog · lexicon · red_flag_rules
├── core/             config · db · security · crypto
├── knowledge/        corpus · chunking · ingest · policy · providers
├── models/           identity · care · clinical · providers · platform
│                     chat · memory · agentic · enums
├── privacy/          boundary.py — the PHI egress architecture
├── seed/             deterministic seeder + sample documents/images
└── services/         ~40 modules incl. detectors/, jobs/, ml/, vision/, delivery/
```

---

## API surface

**112 endpoints** across 15 routers. Interactive docs at
`http://127.0.0.1:8000/docs`.

| Router | Count | Scope |
|---|---|---|
| `care-programmes` | 12 | Enrolment, check-ins, medications, vitals, maternal/elderly dashboards |
| `agent` | 11 | Chat, stream, sessions, memory, status |
| `hospital-admin` | 11 | Dashboard, forecast, no-show, capacity, model quality, referrals |
| `system-admin` | 11 | Users, provider verification, facilities, AI config, audit |
| `medical-records` | 10 | Documents, images, heatmap, vision adapters |
| `providers` | 10 | Doctors, hospitals, diagnostic centres, specialties, tests, slots |
| `doctor` | 8 | Dashboard, queue, patients, pre-consultation, consultations, referrals |
| `guardian` | 8 | Dependents, alerts, and the patient-side consent endpoints |
| `confidential` | 7 | Anonymous sexual-health sessions |
| `appointments` | 5 | Book, reschedule, status, list, get |
| `auth` | 5 | Register, login, refresh, me |
| `patients` | 5 | Dashboard, history, notifications |
| `symptoms` | 4 | Legacy intake sessions |
| `agent-actions` | 3 | Proposals: list, approve, reject |
| `system` | 2 | Root, health (includes live provider status) |

---

## What is real vs placeholder

The project documents this honestly rather than overstating. **Real:** red-flag
engine, care navigation, provider matching, appointment lifecycle (all 8 states
with enforced transition guards), OCR pipeline, no-show prediction, demand
forecasting, knowledge retrieval, assistant conversation, guardrails, PHI
boundary, private chat, agent reasoning, model evaluation, autonomy layer, agent
actions, encryption at rest.

**Conditional:** web search works when `TAVILY_API_KEY` is set; natural model
wording when any LLM key is set, deterministic composers otherwise.

**Not fully wired:**

| Component | Status |
|---|---|
| SMS delivery | Path real, gateway not connected — routing, quiet hours, escalation and the no-clinical-content rule all run; the no-op provider records attempts |
| Pneumonia CV model | **Baseline placeholder** — untrained heuristic, self-labelled, replaceable by dropping in an `.onnx` file |

### Seeded dataset

Fictional, Sri Lankan, deterministic (fixed RNG seed). **No real doctor,
hospital or patient.** 3,000 patients · 70 doctors · 16 hospitals · 10
diagnostic centres · ~68,000 appointments (57,539 historical, 10,423 upcoming) ·
300 consultations · 593 medications with 21,960 dose records.

### Verification

```bash
cd backend && PYTHONPATH=. .venv/bin/python tests/test_scenarios.py
```

Runs seven demo scenarios plus RBAC boundary checks against the live API — 73
checks total.

---

## Disclaimer

SuwaPath provides **health navigation and screening support, not diagnosis**.
The pneumonia adapter shipped by default is an untrained baseline and must not
be used clinically. All seeded people, doctors, hospitals and diagnostic centres
are fictional.
