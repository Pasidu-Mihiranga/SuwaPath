# SuwaPath — video script

Shot-by-shot, with word-for-word narration. Target **14:00**. Ordered
AI-capability-first, because this is an AI competition — the clinical NLP, the
agent, the document AI and the vision model come before the role tour, and the
roles then show those capabilities reaching five different people.

Every figure below was read from a live instance. Re-check before recording if
you reseed.

**Read this first:** run the prep in [DEMO.md](DEMO.md) §1, especially
`scripts.demo_prep`. Without it, shots 22 and 26 are empty.

---

## Shot list

| # | Time | Page | Beat |
|---|---|---|---|
| 1 | 0:00 | `/login` | Hook |
| 2–4 | 0:30 | `/patient/symptom-check` | Clinical NLP, multilingual, deterministic urgency |
| 5–8 | 2:30 | `/patient/assistant` | **The agent loop — centrepiece** |
| 9–11 | 5:00 | `/patient/reports` | Document AI |
| 12–14 | 6:30 | `/patient/imaging` | Vision AI |
| 15–16 | 8:00 | `/patient/programmes` | Eligibility reasoning |
| 17–18 | 8:45 | `/patient` `/patient/sharing` | Dashboard, consent |
| 19–20 | 9:30 | `/guardian` | Consent-gated ranking |
| 21–23 | 10:30 | `/doctor` | Cross-role autonomy |
| 24–27 | 12:00 | `/hospital` | Measured ML |
| 28 | 13:15 | `/admin` | Scale |
| 29 | 13:40 | `/patient` | Close |

---

## Act 1 — What it is

### Shot 1 · 0:00–0:30 · `/login`, signed out

*Slow scroll down the login page. Do not sign in yet.*

> Healthcare fails people before they ever reach a doctor. Is this urgent?
> Which specialist? Which hospital actually has the scanner I need?
>
> SuwaPath answers that. It takes a symptom, a lab report, or an X-ray, and
> navigates one person to the right verified doctor, at a facility that can
> actually run the test they need.
>
> Everything you'll see is synthetic. The demo credentials are printed on the
> login page because there is no real patient data in this system.

*Click **Patient · Nimali Fernando**, then Sign In.*

---

## Act 2 — The clinical AI

### Shot 2 · 0:30–1:30 · `/patient/symptom-check`

*Type slowly enough to read:*

```
For two weeks I have had a productive cough with yellow phlegm, fever every
evening around 38.5, sharp chest pain when I breathe in deeply, and
breathlessness climbing stairs.
```

> It doesn't hand you a form. It takes a history the way a clinician does —
> targeted questions — and it decides for itself when it has enough to assess.
>
> Underneath, every turn runs concept extraction over everything said so far.
> Not keyword matching — concepts, with negation handling. "No blood in the
> phlegm" is recorded as an *absent* finding, not a present one.

### Shot 3 · 1:30–2:00 · same page, result visible

*Point at the urgency banner.*

> Now the part that matters most in a medical tool.
>
> The agent chooses what to do at every other step. There is exactly one thing
> it is not allowed to choose — how urgent you are. That comes from 23
> auditable clinical rules running over the patient's own words, and no model
> output can move it.
>
> A language model writes the explanation. It does not decide the care level.
> If every model provider went down right now, this urgency would still be
> correct.

*If challenged on "rule-based":*

> Take the rules away and the system still routes, plans, calls tools, loops
> and proposes — fully agentic, and unsafe. Take the model away and it stops
> choosing anything at all. The agency is in the model half. The rules only
> fence it.

### Shot 4 · 2:00–2:30 · switch language to සිංහල

**Use this exact sentence.** It is verified end to end — it returns
`urgency: emergency`, `source: red_flag_engine`, the same as the English:

```
මට දින දෙකක් තිස්සේ දරුණු පපුවේ කැක්කුම සහ හුස්ම ගැනීමේ අපහසුතාවක් තිබේ
```

> Sinhala and Tamil aren't a translation layer bolted on top. Input in any of
> the three languages fires the identical clinical rules, because matching
> happens on concepts, not on translated strings.
>
> Same two concepts extracted. Same rule triggered. Same emergency
> classification — from the same rule engine, not a second one.
>
> For Sri Lanka that isn't a nice-to-have. It's the difference between a tool
> people use and one they don't.

> [!WARNING]
> **Do not improvise the Sinhala.** The lexicon indexes `පපුවේ කැක්කුම` and
> `පපුව රිදෙනවා` for chest pain. Writing `පපුවේ වේදනාවක්` — a perfectly
> normal way to say it — extracts **no chest-pain concept**, and the urgency
> drops to routine. Verified: that exact substitution turned an emergency into
> a routine.
>
> Coverage is synonym-based, so it is as good as its synonym list and no
> better. If asked, say that — it is a fair limitation and a much better
> answer than being caught by a live improvisation.

---

## Act 3 — The agent

### Shot 5 · 2:30–3:15 · `/patient/assistant`

*Paste this exact message. It is chosen deliberately — it has no "where should
I go", which would route to the admin branch and skip the loop.*

```
For two weeks I have had a productive cough with yellow phlegm, fever every
evening around 38.5 degrees, sharp chest pain when I breathe in deeply, and
breathlessness when I climb stairs. No blood in the phlegm, no recent travel,
no chest injury, and I am not taking any medication for it.
```

> This is not a chatbot with a search box attached.

### Shot 6 · 3:15–4:15 · **open the trace panel** — the most important shot

*Zoom into the trace. Hold on it.*

> This is the agent's actual execution trace, from this run.
>
> It routed the question to two agents in parallel. They merged. Then a
> bounded reason-act-observe loop took over and ran four steps — it chose
> medications, then the clinical knowledge base, then the facility directory.
>
> Look at step four. The planner tried to call the knowledge base again with
> the same arguments, and the loop **refused it**.
>
> That is the whole argument in one line. A scripted pipeline cannot refuse
> itself. This is a planner choosing its next tool from what it has already
> learned, hitting a guard that exists because repeating a call is the classic
> way these loops fail.

### Shot 7 · 4:15–4:45 · still on the trace

> One more property, and it's the one I'd defend hardest.
>
> While the planner is deciding, it sees only *metadata* about each lookup —
> which tool ran, whether it worked, how many rows came back. Step two,
> knowledge, ok, one result. It never sees the contents.
>
> So a medical record cannot leak into a web-search prompt. Not because a
> policy forbids it — because the content was never in the planning prompt to
> leak. The records are read once, at the end, under one route's field
> allowlist.

### Shot 8 · 4:45–5:00 · the judge — **opportunistic, do not script**

> [!IMPORTANT]
> The judge fires on what the model actually generated, so it is **not
> reproducible on demand**. The same question triggered `regenerate` on one
> run and `allow` on the next. Do not build a scripted beat around it.
>
> Record several turns, check each trace for a `judge` event with verdict
> `regenerate` followed by `revise`, and cut that one in if you catch it. If
> you don't catch one, drop this shot — Act 3 stands without it.

*If you do catch it:*

> And the answer gets reviewed before you ever see it. Here the judge flagged
> the draft as overconfident — stating a definitive diagnosis — sent it back,
> and the revision changed it. Self-correction, in a real run.

---

## Act 4 — Document AI

### Shot 9 · 5:00–5:40 · `/patient/reports`, open `cbc_report.pdf`

> Upload a lab report and it reads it. PDFs use their embedded text layer when
> there is one — that's exact, not guessed. Photographs and scans fall back to
> OCR.
>
> Then it does the harder part: it parses the table while preserving row
> order, and pulls out each analyte with its unit and its range.

### Shot 10 · 5:40–6:10 · point at the flagged rows

> Haemoglobin 10.2, flagged low. Serum ferritin 14, flagged critical. Serum
> iron 42, low. ESR 28, high.
>
> That is a textbook iron-deficiency picture, and the system assembled it from
> a PDF.

### Shot 11 · 6:10–6:30 · point at a reference range

> Here's the detail I'd want a judge to notice. Every one of those ranges came
> from **the patient's own report** — the field literally records
> `reference_source: report`.
>
> Labs differ. A hardcoded normal range flags the wrong things at a lab that
> uses different bounds. It only falls back to an internal catalogue when the
> report doesn't print one.

---

## Act 5 — Vision AI

### Shot 12 · 6:30–7:00 · `/patient/imaging`, upload the chest X-ray

> Chest X-ray screening. First it validates that this is even a radiograph —
> a selfie, a screenshot or a document scan is rejected before any analysis
> runs.

### Shot 13 · 7:00–7:45 · the result, **scroll to "What the model measured"**

> And it shows its work.
>
> Left-right asymmetry: 0.149 — the strongest signal here. Lower-zone opacity:
> 0.098. Texture heterogeneity: 0.178. Each bar is how much that feature moved
> the score.
>
> These are real radiographic features computed from the pixels — density in
> the lower lung fields against the upper zones, asymmetry between left and
> right skipping the mediastinum, and block-wise texture variation.
>
> Score 0.823, against a decision threshold of 0.5.

*Be straight about the model. It costs you nothing and protects everything:*

> This is the transparent baseline classifier, and the interface says so. A
> trained network drops into a folder and takes over on restart. What I'd
> point at is the **explainability** — a clinician can look at this and say
> "that asymmetry is rotation, not consolidation." You cannot argue with a
> bare probability.

### Shot 14 · 7:45–8:00 · the heatmap

> And an occlusion-sensitivity heatmap: every region masked in turn to see
> which ones actually change the score. That's where the model was looking.

---

## Act 6 — Reasoning about care

### Shot 15 · 8:00–8:25 · `/patient/programmes`

> Nimali is at week 19. Next milestone, the week-20 anomaly scan. Danger-sign
> check-ins run against a maternal rule set.

### Shot 16 · 8:25–8:45 · scroll to **Other programmes**

> The programmes she can't join aren't hidden, and they don't fail silently
> when she submits. The system computes eligibility from her record and gives
> the reason.
>
> Three answers, not two. Eligible. Ineligible. And *confirm* — plausible but
> unverified, where she ticks a box and that acknowledgement is stored on the
> enrolment.
>
> That third one is the design. A hard block just gets worked around by
> putting false data in the record, and that's worse than an override you can
> audit.

### Shot 17 · 8:45–9:10 · `/patient`

> Everything lands on one dashboard. And none of it was written by a seed
> script — the recommendation came from the navigation engine, the values from
> the OCR pipeline, the screening from the vision model. Demo data the product
> couldn't have produced is a lie about the product.

### Shot 18 · 9:10–9:30 · `/patient/sharing`

> Consent is scoped and deny-by-default. This screen is what the next role
> sees through.

---

## Act 7 — The other four roles

### Shot 19 · 9:30–10:00 · `/guardian`

*Sign out → **Guardian · Nimal Fernando**.*

> Two dependents, ranked — not listed alphabetically. The ranking blends
> unacknowledged alerts, missed doses, reported warning signs.
>
> An early version weighted every alert equally and one dependent scored 164.
> The noisiest person would have ranked first forever. Every component is
> capped now.

### Shot 20 · 10:00–10:30 · a dependent detail

> And every component is gated by the consent scope actually granted. If he
> wasn't given medication visibility, missed doses don't contribute — because
> otherwise the *ranking itself* leaks a problem he was never allowed to see.

### Shot 21 · 10:30–11:00 · `/doctor`

*Sign out → **Doctor · Dr. Dileepa Perera**.*

> 18 patients today. 5 urgent. 4 reports waiting for review. The queue is
> ordered clinically, not first-come-first-served.

### Shot 22 · 11:00–11:45 · **the doctor's Actions panel**

> *"Two follow-ups have lapsed — send a recall?"*
>
> Nobody asked for this. A consultation recorded a follow-up date. The date
> passed. No appointment was booked. Eight detectors run on a schedule, and
> one of them noticed.
>
> And notice who it's addressed to. The data belongs to the patient. The
> proposal went to the **doctor** — the person who can act on it.
>
> A chatbot waits to be spoken to. This didn't.

### Shot 23 · 11:45–12:00 · patient detail

> A detector reads and enqueues. It never acts. Turning that into anything
> real needs a human to approve it, and authority is re-derived at execution —
> never inherited from whoever raised it.

### Shot 24 · 12:00–12:20 · `/hospital`

*Sign out → **Hospital admin · Chathurika Bandara**.*

> 639 appointments booked in the period, with capacity warnings by specialty.

### Shot 25 · 12:20–13:00 · `/hospital/no-show`

> Every appointment tomorrow is scored for no-show risk by a logistic model
> fitted on this hospital's own history.
>
> And it's measured. AUC 0.624. Brier score 0.165. Expected calibration error
> 0.025. On 1,867 held-out appointments, split by time so nothing from the
> future leaks backwards.
>
> 0.62 is a modest AUC and I'll say so. It's a real number from a real
> backtest, which is worth more than a better number nobody can reproduce.

### Shot 26 · 13:00–13:15 · the hospital's Actions panel

> *"Remind 13 patients about Wednesday?"* One batched proposal per hospital
> per day — not thirteen separate approvals, which is exactly the alert
> fatigue the system caps elsewhere to avoid.
>
> And approving it increments the reminder count that is itself a feature of
> the no-show model. Before this existed, that feature was a permanent zero.
> The loop closes.

### Shot 27 · 13:15–13:30 · `/admin`

> 3,210 users. 70 doctors, 16 hospitals, 10 diagnostic centres, 68,580
> appointments. Not three demo rows in a table.
>
> Every consent change and every proposal decision is audited.

---

## Act 8 — Close

### Shot 28 · 13:30–14:00 · back to `/patient`

> Clinical NLP that works in three languages. An agent that plans, calls
> tools, and refuses its own repeated work — that cannot leak a record into a
> web prompt because the content is never in the planning prompt. Document AI
> that reads ranges off your own report. Explainable image screening. A
> calibrated risk model with its numbers stated. And a layer that notices what
> nobody reported, and asks before acting.
>
> One question, answered for five different people. Where should this person
> go, and when.

*End on the patient dashboard. Finish on the person, not the admin console.*

---

## Editing notes

**Hold the trace shot.** Shot 6 is the single most important frame. Give it 8
seconds on screen, zoomed, before you start talking over it.

**Zoom on numbers.** The OCR flags, the three CV features, the AUC line. They
are unreadable at full-screen 1280px in a compressed upload.

**Cut the loading.** The agent takes 2–4 seconds per turn. Cut it.

**Show, then say.** Let each screen land for a beat before the narration
starts. Talking over a transition loses both.

**Sign-outs are dead air.** Cut straight from one dashboard to the next.

**Subtitle the numbers.** AUC 0.624, Brier 0.165, ECE 0.025, ferritin 14.
Spoken figures don't survive compression, and judges scrub.

---

## Q&A prep

**"Is the CV model trained?"**
No — it's the transparent baseline, and the UI labels it. The pipeline,
validation, explainability and hand-off are all real; trained weights drop in
and take over on restart.

**"Isn't this just a rule-based system?"**
Only urgency is. Remove the rules and it still routes, plans, loops and
proposes. Remove the model and it stops choosing. The trace in shot 6 is a
planner choosing tools and refusing its own repeat.

**"Does the agent loop on every message?"**
No. It runs once a consultation reaches a conclusion, because running it on
every turn produced lookups nothing needed. That's a deliberate cost decision.

**"AUC 0.624 is low."**
It is. It's honest, time-split, and reproducible. Most no-show baselines in
this literature sit in the 0.6–0.7 band.

**"What happens with no API key?"**
Every feature still runs, on deterministic engines and written composers. The
agent degrades to fixed behaviour rather than failing — but be precise: on
that path it is not a loop.
