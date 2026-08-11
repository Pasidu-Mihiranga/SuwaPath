# Recording a SuwaPath demo

A working script for a 12–15 minute walkthrough covering all five roles.
Every number quoted here was read from a seeded database on 11 Aug 2026 — if
yours differs, the shape is the same but re-check before you narrate a figure.

The order below is deliberate: **patient → guardian → doctor → hospital →
admin**. It follows one clinical situation outward from the person it happens
to, so each role inherits context from the last. Demoing role by role in
alphabetical order makes five unrelated tours instead of one story.

---

## 1 · Before you press record

Run these in order. The last one is the step people skip, and skipping it is
what makes the most interesting part of the system look dead.

```bash
cd backend
.venv/bin/python -m scripts.migrate
```

```bash
.venv/bin/python -m app.seed.seeder --reset
```

```bash
.venv/bin/uvicorn app.main:app --port 8000
```

With the API up, in a second shell:

```bash
cd backend && .venv/bin/python -m app.seed.demo_journeys
```

```bash
cd backend && .venv/bin/python -m scripts.demo_prep
```

### Why `demo_prep` matters

The autonomy layer has two stages. A detector notices something and
**enqueues a task**; a worker later claims that task and turns it into the
**proposal a human sees**. Between those stages the work is real, sitting in
`agent_tasks`, and completely invisible in the UI.

On the database used to write this, 379 tasks were sitting unprocessed — 14
no-show reminder batches, 64 lapsed follow-ups, 300 medication checks. Every
Actions panel except the patient's was empty. Recording that would have shown
a system that notices nothing, which is the opposite of true.

`demo_prep` runs both stages and then prints exactly what each account will
have on screen. Expect:

```
patient@suwapath.lk      book_appointment ×3
doctor@suwapath.lk       send_followup_recall ×1
hospital@suwapath.lk     send_appointment_reminders ×1
```

If those three lines are missing, stop and fix it before recording.

### Setup for the camera

- Browser at **1280×800 or wider**. Below `lg` the care-programme grid drops
  to two columns and the sidebar collapses.
- **Zoom 100%**, one clean window, no bookmarks bar.
- Sign in with the **demo buttons** on the login page, not by typing — it is
  faster on camera and shows the credentials are published.
- Password for every account: `Demo@1234`.

---

## 2 · The flow, scene by scene

### Scene 0 — What this is (0:00–0:45)

Open on the login page without signing in.

> SuwaPath is a healthcare navigation platform for Sri Lanka. It answers one
> question — *where should this person go, and when* — for five different
> people who each need a different answer: the patient, the family member
> caring for them, the doctor, the hospital running the clinic, and the
> administrator running the platform.
>
> Everything you will see is synthetic data. The credentials are published on
> the login page because there is no real patient information in this system.

Point at the four demo buttons. Don't linger.

---

### Scene 1 — Patient (0:45–5:30)

Click **Patient · Nimali Fernando**.

#### 1a. Dashboard `/patient`

> Nimali is 32, in Colombo, 19 weeks pregnant.

Name what is on screen: upcoming appointments, her current recommendation, her
most recent lab report, a chest X-ray screening, her care programme, and
medication reminders.

> Nothing here was written by a seed script pretending to be a patient.
> Every one of these rows was produced by the application itself — the
> recommendation came out of the navigation engine, the report values came out
> of the OCR pipeline, the screening came out of the vision model. Demo data
> the product could not have generated is a lie about the product.

#### 1b. Symptom check `/patient/symptom-check`

**This is the most important 90 seconds in the video.** Type a symptom
description with a red flag in it — chest pain with breathlessness works.

> The system takes a history, then assesses. And the critical detail: the
> urgency level is **never** set by a language model. It comes from a
> deterministic rule engine — 24 rules, source-readable, that always produce
> the same answer for the same input.
>
> A language model writes the explanation. It does not decide how urgent you
> are. If the model is unavailable, the urgency is still correct, and the
> system falls back to written composers rather than guessing.

That distinction is the strongest safety claim the project has. Say it slowly.

#### 1c. Assistant `/patient/assistant`

Ask something that needs a lookup — *"where can I get an MRI in Colombo"*.

> This is an agent, not a chatbot with a search box. It plans, calls a tool,
> looks at what came back, and decides whether it needs another one, up to a
> bounded number of steps with a wall-clock deadline.
>
> One property worth stating: while it is planning, the planner sees only
> *metadata* about each tool result — which tool ran, whether it succeeded,
> how many rows came back. It never sees the content. That makes it
> structurally impossible for a medical record to leak into a web-search
> prompt, rather than merely against the rules.

Then switch the composer to **private mode**.

> A private session is encrypted with a key derived from the patient's own
> PIN. One PIN, any number of sessions. The server cannot read these
> conversations, and neither can anyone with database access.

Show the **Actions** panel with its 3 pending suggestions.

> These were not requested. The system noticed unconverted referrals and
> prepared bookings — a specific doctor, a specific slot, a specific price —
> and then stopped and asked. It proposes; a human approves.

#### 1d. Care Programmes `/patient/programmes`

> Nimali is at week 19. Her next milestone is the week-20 anomaly scan.

Scroll to **Other programmes**.

> Look at the cards she cannot join. The system does not hide them and does
> not silently fail when she submits — it computes eligibility from her record
> and tells her the reason.
>
> There are three answers, not two. Eligible. Ineligible — a contradiction the
> record cannot resolve. And *confirm*, for anything plausible but unverified,
> where she ticks a box and that acknowledgement is stored on the enrolment.
>
> That third answer is the whole design. A hard block gets worked around by
> putting false data in the record, which is worse than an override you can
> audit.

#### 1e. Reports and Imaging

`/patient/reports` — open the CBC report, show the extracted values.

`/patient/imaging` — open the chest X-ray with its heatmap.

> Be direct here: **this is the untrained baseline model.** The adapter
> reports `is_trained_model: false` and the interface labels it as a baseline.
> Trained weights drop into a folder and the ONNX adapter takes over on
> restart. Claiming a trained model here would be the easiest thing in this
> demo to catch.

#### 1f. Sharing & Consent `/patient/sharing`

> Consent is scoped and deny-by-default. This screen is what the guardian view
> you are about to see is derived from.

Leave `/patient/history`, `/patient/notifications`, `/patient/profile` and
`/patient/settings` for the reference section — mention them in one sentence
and move on. They are ordinary CRUD and spending time there costs you the
scenes that are not.

---

### Scene 2 — Guardian (5:30–7:00)

Sign out, click **Guardian · Nimal Fernando**.

#### 2a. Dependents `/guardian`

Two dependents: Sunil (69) and Dilini (28, pregnant).

> Nimal sees these two ranked, not listed alphabetically. The ranking blends
> unacknowledged alerts, missed medication and reported warning signs.
>
> An early version weighted every alert at 4.0 and one dependent scored 164 —
> the noisiest person would have ranked first forever, regardless of how ill
> anyone was. Each component is capped now.

#### 2b. Dependent detail `/guardian/dependents/:id`

> **Every component of that ranking is gated by the consent scope actually
> granted.** If Nimal has not been granted medication visibility, missed doses
> do not contribute — otherwise the ranking itself leaks the existence of a
> problem he was never permitted to see.

#### 2c. Alerts `/guardian/alerts`

Show them, keep it short.

---

### Scene 3 — Doctor (7:00–9:30)

Sign out, click **Doctor · Dr. Dileepa Perera**.

#### 3a. Dashboard `/doctor`

Real figures: **18 patients today, 5 urgent, 2 follow-ups due, 4 reports
pending review**, workload 11% complete.

#### 3b. Queue `/doctor/queue`

> 18 in the queue, 5 flagged urgent. The ordering is clinical, not
> first-come-first-served.

#### 3c. The proposal — **do not skip this**

Open the Actions panel.

> *"2 follow-ups have lapsed — send a recall?"*
>
> Nobody asked for this. A consultation recorded a follow-up date, the date
> passed, no appointment was booked, and the system noticed and drafted the
> recall.
>
> This is the answer to the criticism that the project is a chatbot. A chatbot
> waits to be spoken to. This noticed something in the record that no human
> had looked at, and addressed a proposal to a **different role** than the one
> the data belongs to.

#### 3d. Patients `/doctor/patients` → a patient detail

50 patients. Open one.

> Note what a doctor sees and what they don't. A doctor cannot see a proposal
> addressed to their patient — authority is re-derived at execution and never
> inherited from whoever sent the message.

---

### Scene 4 — Hospital admin (9:30–11:30)

Sign out, click **Hospital admin · Chathurika Bandara** — LankaCare Central.

#### 4a. Dashboard `/hospital`

**639 appointments booked** in the period, with capacity warnings — obstetrics
and gynaecology predicted at 64.

#### 4b. No-show `/hospital/no-show`

> Every appointment tomorrow is scored for no-show risk by a logistic model
> trained on the platform's own history.
>
> Measured, not asserted: **AUC 0.624, Brier 0.165, expected calibration error
> 0.025** on 1,867 held-out appointments, on a time-based split so no future
> data leaks backwards.
>
> 0.62 is a modest AUC and worth saying out loud. It is a real number from a
> real backtest, which is worth more than a better number nobody can
> reproduce.

If you have 20 spare seconds, the honest bug story lands well:

> The first version of this scored 100% no-shows in both arms. An automatic
> sweep was marking every elapsed appointment as missed, including ones the
> model had just scored — the labels were being written by the thing being
> measured. Appointments now record where their status came from, and
> sweep-derived ones are excluded from every label set.

#### 4c. The proposal

> *"Remind 13 patients about Wed 12 Aug?"*
>
> One batched proposal per hospital per day, not thirteen separate approvals —
> twenty buttons is exactly the alert fatigue the system caps elsewhere to
> avoid.
>
> And approving it increments the reminder count that is **feature index 8 of
> the no-show model itself**. Before this existed that feature was a permanent
> zero. The loop closes.

#### 4d. Forecast, Capacity, Providers

One line each. `/hospital/forecast` is demand projection, `/hospital/capacity`
is utilisation, `/hospital/providers` is the roster.

---

### Scene 5 — System admin (11:30–13:00)

Sign out, sign in as `admin@suwapath.lk`.

#### 5a. Overview `/admin`

> **3,210 users. 70 doctors, 16 hospitals, 10 diagnostic centres. 68,580
> appointments. 17 specialties, 27 diagnostic tests, 4 care programmes.**
>
> This is not three demo rows in a table.

#### 5b. AI configuration `/admin/ai`

> Which model is in use, what the vision model's operating point is, and
> whether it is a trained model or the baseline. An operating point that is
> invisible is an operating point nobody checks.

#### 5c. Audit `/admin/audit`

> Every consent change, every proposal decision, every administrative action.
> This is also the event log the directory re-index detector reads.

#### 5d. Users, Providers, Facilities

One sentence. Verification workflows and CRUD.

---

### Scene 6 — Close (13:00–14:00)

> Five roles, one clinical situation. A deterministic safety engine that a
> language model cannot override. An agent that plans and calls tools under
> bounds, and structurally cannot leak records into a web prompt. A layer that
> notices things nobody reported and asks before acting. And measured ML with
> the numbers stated, including the ones that are only adequate.

End on the patient dashboard, not the admin screen — finish on the person.

---

## 3 · Page reference

Pages not in the main flow, so you can answer questions without hunting.

### Patient — 14 routes

| Route | What it is |
|---|---|
| `/patient` | Dashboard — the six-card summary |
| `/patient/symptom-check` | History-taking + deterministic red-flag engine |
| `/patient/assistant` | Agent chat, private mode, Actions panel |
| `/patient/find-care` | Doctor/facility matcher with stated reasons |
| `/patient/appointments` | Booking and history |
| `/patient/reports` | Uploads with OCR-extracted values |
| `/patient/imaging` | Image screening with heatmap |
| `/patient/programmes` | Enrolment, eligibility, milestones, check-ins |
| `/patient/history` | Longitudinal record |
| `/patient/sharing` | Consent scopes — deny-by-default |
| `/patient/notifications` | 33 unread on this account |
| `/patient/profile` | Demographics, including the optional Sex field |
| `/patient/settings` | Language, PIN, preferences |
| `/private` | Anonymous confidential pathway — **no sign-in** |

`/private` is worth 15 seconds on its own: the confidential sexual-health
route is reachable without an account, which is the entire point of it, and it
is the one programme eligibility never gates.

### Guardian — 6 routes
`/guardian` · `/guardian/dependents/:id` · `/guardian/alerts` ·
`/guardian/notifications` · `/guardian/profile` · `/guardian/settings`

### Doctor — 8 routes
`/doctor` · `/doctor/queue` · `/doctor/appointments` · `/doctor/patients` ·
`/doctor/patients/:id` · `/doctor/notifications` · `/doctor/profile` ·
`/doctor/settings`

### Hospital — 8 routes
`/hospital` · `/hospital/forecast` · `/hospital/no-show` ·
`/hospital/capacity` · `/hospital/providers` · `/hospital/notifications` ·
`/hospital/profile` · `/hospital/settings`

### Admin — 9 routes
`/admin` · `/admin/users` · `/admin/providers` · `/admin/facilities` ·
`/admin/ai` · `/admin/audit` · `/admin/notifications` · `/admin/profile` ·
`/admin/settings`

---

## 4 · Known rough edges

Better to know these than to meet them live.

**The patient's three proposals share one title.** All three read "Book your
Urgent medical assessment appointment?" — different doctors, different
facilities, different source recommendations, identical heading. On camera it
reads as a duplicate bug. Either expand one to show the bodies differ, or show
only the first.

**Guardian and system admin have no pending proposals.** The detectors that
would address them need conditions this data does not meet. Do not promise
"every role gets autonomous proposals" — three of five do, and that is still
the point being made.

**A gentle symptom history may not conclude.** The engine assesses when it has
enough; a mild history can reach the turn limit still asking questions. Use a
symptom description with a clear red flag, and rehearse the exact wording.

**The vision model is the untrained baseline.** Say so before anyone asks.

**Uploaded files do not survive a redeploy** on the hosted version. Fine for a
recording; worth knowing if you demo live after a deploy.

**`scripts/migrate.py` is not run automatically** by the Dockerfile, the
compose file or app startup. After deploying, run it by hand or the deployed
database will be missing schema and seeded-content fixes.
