# SuwaPath — how it actually works

Direct answers to the questions a reviewer asks, with the numbers behind them.
Where something is weaker than it sounds, this says so — a claim that cannot
survive being checked is worth less than a limitation stated up front.

Everything here was read off the running system, not from memory.

---

## Contents

- [Why a multi-agent system — and does it need one?](#why-a-multi-agent-system--and-does-it-need-one)
- [Is the tool calling real, or just JSON?](#is-the-tool-calling-real-or-just-json)
- [Rules or LLM — which decides what?](#rules-or-llm--which-decides-what)
- [How the agents actually communicate](#how-the-agents-actually-communicate)
- [What happens when a provider fails](#what-happens-when-a-provider-fails)
- [The knowledge base: what is in it, how it was built](#the-knowledge-base-what-is-in-it-how-it-was-built)
- [Is RAG working?](#is-rag-working)
- [Is CAG working?](#is-cag-working)
- [Is there an LLM judge? What are the guardrails?](#is-there-an-llm-judge-what-are-the-guardrails)
- [How patient data is secured](#how-patient-data-is-secured)
- [If the database leaked tomorrow, what would the attacker have?](#if-the-database-leaked-tomorrow-what-would-the-attacker-have)
- [How history is kept honest — and why not blockchain](#how-history-is-kept-honest--and-why-not-blockchain)
- [Private mode: what is actually in the database](#private-mode-what-is-actually-in-the-database)
- [How the CV model works](#how-the-cv-model-works)
- [Where this should improve](#where-this-should-improve)

---

## Why a multi-agent system — and does it need one?

**Honest answer: the chat path does not need one. Two other things genuinely do.**

The system was benchmarked against the alternative rather than argued about.
Five architectures answered the same 114 labelled vignettes:

| Arm | What it is | Emergency recall | Missed |
|---|---|---|---|
| A | Rules, opening message only | 84.4% | 7/45 |
| A-full | Rules, given every answer up front | 100% | 0/45 |
| B | **One LLM call, no rules** | 76.3% | 9/38 |
| C-generic | Fixed-script questioning | 86.7% | 6/45 |
| C | Rule-directed questioning | **100%** | **0/45** |

Five of the six route agents call a **hardcoded** tool list (`ROUTE_TOOLS`) —
the model chooses nothing there. A single well-prompted call could
approximate that path. Conceding this is not a weakness in the argument; it is
the argument, because it isolates what actually carries the safety.

What the fan-out really buys is narrower and real: **concurrent database reads**
(latency on compound questions) and **per-route PHI minimisation** — each agent
sees only its allowlisted fields, so a route-crossing leak is structurally
impossible rather than merely forbidden. That is a security argument for
separation, not an intelligence one.

**Three things a single call cannot do:**

1. **Decide urgency safely.** Arm B missed 9 of 38 emergencies — postpartum
   sepsis, infant sepsis, PPROM, a GI bleed — and 3 of 4 Sinhala cases, where
   the rule engine missed none in any language.
2. **Act when nobody asked.** Eight detectors run on schedules over a Postgres
   task queue. There is no inbound request for a single call to respond to.
3. **Enforce consent at the query.** Tools filter by consent scope in SQL, so
   no prompt injection changes what returns.

**The defensible thesis is therefore narrower than "we built a MAS":**

> Safety is deterministic. Language is probabilistic. Autonomy is scheduled.

---

## Is the tool calling real, or just JSON?

**Both exist. Native function-calling is real and measured; the JSON protocol
predates it and remains as a fallback.**

| Path | Mechanism | Status |
|---|---|---|
| `NativeToolCallPlanner` | Provider `tools` API, real `tool_calls` | Works — **15/15** correct selections, median **838 ms** |
| `LlmPlanner` | Hand-rolled JSON prompt | Original protocol, still the fallback |
| `DeterministicPlanner` | Fixed handoffs | The zero-key path |
| `ROUTE_TOOLS` | Static dict, all tools called | **Not** model-driven, deliberately unchanged |

The 15/15 covers five scenarios × three trials, including the case models most
often get wrong — correctly choosing to **stop** when a closing message needs
no lookup.

Two things that came out of building it, both worth knowing:

- The model invented `{"specialty_code": "CARDIO"}`, which matches nothing and
  returns silently empty. Specialty codes are now an **enum in the schema**, so
  an invalid code is inexpressible rather than something to validate afterwards.
- Offered the full tool list, it re-requested the same lookups until the step
  budget ran out. **Spent tools are withdrawn from the manifest**, making the
  repeat unaskable rather than discouraged.

**Native tool calling is off by default** (`NATIVE_TOOL_CALLING_ENABLED`). It is
verified to work and is structurally safer; it is *not* verified to be better
than the JSON planner, because that comparison was contaminated by free-tier
rate limits and withdrawn rather than reported.

**Scope is enforced by omission.** The manifest is built from
`allowed_tools(scope)`, so a guardian without medications consent never learns
the tool exists — asserted by tests against the outbound request body, not just
at execution.

---

## Rules or LLM — which decides what?

| Decision | Decided by | Can the LLM override it? |
|---|---|---|
| Urgency | 23 deterministic rules | **No** |
| Which question to ask next | Rule-gap scoring | No |
| Specialty and tests | Catalogue weights | No |
| Facility matching | Capability + distance | No |
| Consent | SQL at the query layer | No |
| Wording of a question | LLM (curated text preferred) | — |
| The explanation narrative | LLM, constrained | — |
| Which tool to call | LLM, from a scoped manifest | — |

The rule engine is 23 rules over 55 concepts. Rules are conjunctions of concept
groups with optional context predicates (pregnancy, age, chronic conditions),
evaluated independently — **highest urgency wins**.

### How the next question is chosen

Not a fixed list. For each rule the system computes **distance to firing** —
how many concepts are still missing — and scores candidate concepts by
`urgency.rank / needed²`, accumulated across rules. A concept that would close
several rules at once rises to the top. Rules whose context excludes the patient,
or whose only remaining option they have denied, are **dropped**, not merely
ranked low.

Measured against the fixed script on emergencies hidden behind an ordinary
opening: **8/8 caught versus 1/8**, for 0.6 extra questions on an average
non-emergency consultation.

---

## How the agents actually communicate

**Honestly: they do not talk during execution. One place passes output between
them, and it is now recorded.**

The parallel branches are dispatched by LangGraph `Send()` and cannot see each
other — that is the price of concurrency, not an oversight. Results accumulate
into `agent_outputs` via an `operator.add` reducer and are combined at `merge`.

The one place one agent's conclusion becomes another's input is `fulfil`, the
bounded ReAct loop that runs after merge. That handoff is the inter-agent
communication in this design, and it is now recorded with the fact that moved:

```
consult → find_care   passed 'Gastroenterology'         because the consultation concluded Gastroenterology
consult → directory   passed 'Ultrasound Abdomen, ...'  because the consultation suggested those tests
```

It used to record `from: "loop", because: "step 2"` — true, and useless.

**What the planner may see is deliberately limited:** step, tool, status and
result *count* — never the contents. Planning needs to know whether a lookup
returned anything, not what. Because content never enters the planning prompt,
a route-crossing PHI leak is structurally impossible rather than forbidden by
policy.

**Bounds, and why each exists:** 4 steps, 6 tool calls, a 12-second deadline
checked between steps, and a repeat-call detector that refuses an identical
call with an observation saying so. Free-tier providers are the real latency
risk; the deadline is what makes the worst case survivable.

---

## What happens when a provider fails

Nothing hard-fails. Every layer degrades:

| Layer | Fallback |
|---|---|
| Language model | Groq → OpenRouter → Gemini → **deterministic composer** |
| Tool planning | Native → JSON → fixed handoffs |
| Retrieval | Qdrant + MiniLM → **TF-IDF index** |
| Question wording | Curated text → LLM → template |
| CV model | ONNX weights → bundled baseline |

A failed provider is put on a 60-second cooldown rather than retried on every
request, because free-tier failures are usually per-minute rate limits.

This was demonstrated accidentally and is the strongest evidence for the
design: the evaluation ran while the free tiers were exhausted, so most
assessments fell back to the deterministic composer. **Urgency accuracy was
unchanged** — the patient got plainer wording and the same correct triage. The
single-LLM arm under the same conditions returned nothing at all.

---

## The knowledge base: what is in it, how it was built

**There is no external medical database.** No SNOMED, no ICD-10, no licensed
content. Every clinical fact is hand-written in this repository — about 1,600
lines across four files.

| File | Size | Holds |
|---|---|---|
| `clinical/lexicon.py` | 55 concepts | Concept → the words patients type, in en/si/ta including romanised |
| `clinical/red_flag_rules.py` | 23 rules | Concept combinations → urgency |
| `clinical/catalog.py` | — | Concept → specialty, capabilities, tests with LKR prices |
| `knowledge/corpus*.py` | **74 passages, 6,310 words** | Retrievable patient guidance |
| `clinical/hypothesis.py` | 34 curated questions | Concept → the question that asks about it |

The corpus is in three parts: general WHO-aligned patient education, a
**Sri Lanka set** (dengue and its warning-sign phase, leptospirosis, snakebite,
rabies, TB, CKDu, maternal danger signs, the PHM route, OPD vs channelling vs
1990 Suwa Seriya), and platform policy.

**Written to patient-education standards, not clinical protocol**: it says when
to seek care and what to expect, never how to treat, and **no medicine or dose
appears anywhere** — matching the rule the rest of the platform follows.

**The honest limitation.** 23 rules is small. Manchester Triage and NHS Pathways
have hundreds, built and maintained by clinicians. **Neither the corpus nor the
first-aid scripts are clinician-reviewed**, and both files say so in their
headers. What is defensible is not the volume but the property: the rules are
explicit, inspectable, testable and multilingual by construction — which is why
the single-LLM arm missed 9 emergencies and the rule engine missed 0.

---

## Is RAG working?

**Yes — and it was silently broken until it was measured.**

Qdrant with MiniLM embeddings, three collections kept separate (clinical
knowledge, provider directory, platform policy) because mixing them lets "which
doctor treats asthma?" retrieve an article *about* asthma with no doctor in it.
Passages are chunked so each vector covers one idea. A TF-IDF cosine index takes
over when embeddings are unavailable.

Two real bugs, both found by testing rather than reading:

1. **One relevance floor was applied to both backends.** Embedding cosine and
   TF-IDF cosine are not the same quantity — a *correct* top hit scored 0.24 on
   TF-IDF against a 0.35 floor and was discarded. So whenever Qdrant was
   unavailable, **the entire knowledge base went silent while reporting itself
   healthy**. Floors are now per-backend and calibrated against on- and off-topic
   queries.
2. **Lexical matching returned confident nonsense at scale.** "Is it safe to
   breastfeed at night" retrieved the *asthma* passage; "my mother is 80 and
   fell" retrieved *pregnancy danger signs*. No threshold fixes a vocabulary
   mismatch — patients write "burnt", the corpus says "burn". Adding stemming
   fixed the retrieval itself.

After the fix, every on-topic query returns the right passage and off-topic
queries return nothing (lowest on-topic 0.155, highest off-topic 0.122 — no
overlap).

**Deployment note:** embedded Qdrant takes an exclusive lock, so only one
process gets it. A server on the usual port is now auto-detected, making
`docker compose up -d qdrant` the whole fix.

---

## Is CAG working?

**Yes, and its restraint is the point.**

Two layers. **Layer 1** returns reviewed answers for non-clinical intents,
matched by normalised text and embedding similarity. **Layer 2** is per-session
memory of what the patient already said, which is what stops the assistant
asking their age three turns running.

Coverage went from 11/22 to **23/24** on the questions people actually type.
The two most important additions are safety, not speed: *"are you a doctor"* and
*"can you prescribe"* must give the same careful answer every time, and a model
improvising them is exactly what should not happen.

**Nothing clinical is ever cached, by design.** Two patients asking whether
their chest pain is serious have near-identical embeddings and opposite correct
answers. Eligibility is enforced by an allowlist of intent kinds
(`greeting | faq | capability`), not by a similarity threshold. Confidential
sessions bypass both layers and write nothing.

---

## Is there an LLM judge? What are the guardrails?

**There is a judge. It is deterministic, not an LLM.** That is deliberate: a
model asked to check another model's output shares its blind spots, and cannot
be audited afterwards.

**Input guard** (`guard_input`, before any model or cache is touched):
prompt-injection attempts, requests to prescribe, self-harm disclosures routed
to crisis support with a real helpline. A crisis input never reaches an agent.

**Output judge** (`judge_output`, after synthesis) checks for PII patterns,
overconfident diagnostic phrasing, and — most importantly — **contradiction of
the rule engine's urgency**. Verdicts are `ALLOW`, `SOFTEN` (one LLM rewrite,
then re-judged) or `BLOCK`. A blocked answer never takes the revise path, so a
bad answer cannot be laundered into a good one by looping.

**The PHI boundary** sits between the application and any model: per-route
field allowlists, name pseudonymisation, and a regex pre-flight for emails,
Sri Lankan phone and NIC numbers and coordinates. A match **blocks the call**
rather than sending it.

---

## How patient data is secured

**Encrypted, not hashed.** Hashing is one-way and the application must be able
to read these values back, so they are encrypted with **AES-256-GCM** at the
column level.

Encrypted at rest: conversation content, and on the patient profile the chronic
conditions, allergies, current medications, past surgeries, family history,
blood group, address and emergency contacts.

**Deliberately not encrypted, and why it matters:** `User.full_name` and
`email`, because the admin console searches them with SQL `LIKE`. AES-GCM is
randomised, so the same value encrypts differently every time — `WHERE column =
'x'` matches nothing, **silently**. An empty result set is not an error. Where a
value must be both encrypted and searchable the pattern is a blind index (HMAC
of the normalised value); nothing here needs one yet.

**Pseudonymisation is a separate mechanism** for a different threat. Before text
reaches a model, names are replaced with per-session salted tokens
(`PERSON_A7B3C1`), reversible only in-process and never persisted. Encryption
protects the database; pseudonymisation protects what leaves the building.

**Key handling.** Ciphertext carries the id of the key that wrote it
(`v2.<key_id>.<nonce>.<ciphertext>`), which is what makes rotation possible
without rewriting the database. A `KeyProvider` interface separates the local
environment key from a KMS; the KMS provider **raises rather than falling back**,
because one that quietly degrades to reading an environment variable is worse
than none.

Startup **fails closed**: with `environment=production` and no key, the
application refuses to start rather than storing plaintext.

---

## If the database leaked tomorrow, what would the attacker have?

| They would get | They would **not** get |
|---|---|
| Names, emails, phone numbers | Conditions, allergies, medications, surgeries, family history |
| City and district | Home address, emergency contacts, blood group |
| Appointment times, specialties | Any conversation content |
| Structure and volume of records | Private-mode transcripts (see below) |
| Bcrypt password hashes | Usable passwords |

**Every clinical field is ciphertext without the key.** That is the difference
between a reportable breach and an incident: under the HIPAA Breach Notification
Rule and GDPR Article 34(3)(a), encrypted health data whose keys were not also
taken is not notifiable. Sri Lanka's **Personal Data Protection Act No. 9 of
2022** imposes the controller obligations that apply here.

**The honest caveats.** Names and emails are plaintext, so a leak still
identifies *who* is a patient even if not *what* they have. And if the attacker
takes the application environment as well as the database, they have the key —
which is the argument for a KMS in a real deployment, and why that interface
exists.

---

## How history is kept honest — and why not blockchain

The audit table records what happened. A **hash chain** records that the record
itself has not since been changed:

```
entry_hash = sha256(prev_hash || canonical(entry))
```

Alter a row, delete one, or reorder history and every subsequent hash stops
matching. **System Admin → Security** verifies the chain on demand and names any
entry that no longer adds up.

What is chained: every record **read** by someone who is not the patient (with
the basis it was allowed under), every **amendment** to a clinical note *with
the previous value*, the moment a note is **completed**, every **emergency
override**, and every message sent to a patient.

Reading is logged, not just writing — the harm in a medical system is usually
someone opening a record they had no business opening, and a log of
modifications cannot show that.

The chain head is a **locked single row**, not "the most recent entry": two
concurrent writers reading the same tip would fork the chain into branches that
each verify in isolation.

**Why not blockchain.** A distributed ledger solves *"no single party is
trusted"* — mutual distrust between parties who must nonetheless agree. That is
not this problem. A regulator wants assurance that **the operator** cannot
quietly alter records, and a hash chain gives exactly that with no consensus, no
nodes and nothing to sync. Adding a blockchain here would be theatre.

**What it does and does not do:** it makes tampering *detectable*, not
impossible. Someone with database rights can still rewrite rows — they simply
cannot do it without either breaking verification or reproducing every
subsequent hash. During testing, an accidental tamper could not be repaired
without supplying the exact original content from outside, which is the property
working.

---

## Private mode: what is actually in the database

For a conversation about an STI, an unplanned pregnancy or mental health, the
server holding a readable copy is the wrong design.

- The key is derived from the user's **6-digit PIN with PBKDF2** (210,000
  rounds) and **never stored**.
- Message bodies are held in **process memory**, not written to the message
  table. Only counters and a `PrivateTranscript` ciphertext row touch disk.
- The session row exists so the transcript can be found, but **the owner cannot
  list it without the PIN** — `GET /agent/sessions/{id}` returns `403` for a
  private session; the resume endpoint is the only way back in.
- PIN verification uses constant-time comparison, with a 5-attempt lockout and a
  15-minute cooldown — a lockout rather than deletion, so the lockout cannot be
  used to destroy someone's transcript.
- Private sessions bypass CAG, write no durable memory, publish nothing to the
  doctor view, and expire in 12 hours.

A database dump **plus** the entire application configuration still does not
open a private transcript. Nothing else in the system has that property, and
nothing else needs it.

---

## How the CV model works

Chest X-ray pneumonia screening. Adapter architecture: drop an `.onnx` into
`models/pneumonia/` and it takes priority over the bundled baseline, with no
code change. The loader reads input shape, channel count and layout from the
graph, so Keras and PyTorch exports both work.

**Three trained variants** — same ResNet50 architecture, different training
data. Selection is by **age only**: whether the chest belongs to a child changes
the anatomy being read, while how the file was captured changes only its quality.
There is deliberately no camera-specific model — the checkpoint that sounded
like one (`combined_noPhone`) was trained with phone photographs *excluded*.

**Heatmaps use occlusion sensitivity**, not Grad-CAM, so a dropped-in graph gets
a visual explanation without needing named convolutional layers.

**The models are not calibrated, and that is visible rather than hidden.** A
threshold is the clinical safety decision and does not transfer between
datasets. Sidecars carry `threshold: null` and the loader logs *"no usable
threshold"* — a missing number is visible, whereas a default 0.5 looks like a
decision somebody made. `scripts/calibrate_pneumonia.py` measures it from a
held-out set and reports what STARD and CLAIM ask for: sensitivity and
specificity with **Wilson intervals**, PPV and NPV with the prevalence that
makes them meaningful.

---

## Where this should improve

Ordered by how much it matters, not by effort.

1. **Clinician review.** 74 corpus passages, 23 rules and 9 first-aid scripts,
   none signed off. The cheapest credibility win available.
2. **Calibrate the CV models.** They ship uncalibrated. Needs a held-out
   labelled set, which is data, not code.
3. **Expand the rule set and lexicon.** An unrecognised symptom currently
   becomes `self_care` — **under-triage by omission**. Two known gaps are
   recorded in the gold set rather than quietly fixed: no sleep/insomnia
   concept, and negation scoping inside a noun phrase ("no blood in the cough"
   drops the cough).
4. **Grow the multilingual gold set.** Sinhala and Tamil have 5 and 3 vignettes.
   The direction is clear; the magnitude is not publishable.
5. **Clinician-validated labels.** The gold set is self-authored, and the
   `positive` vignettes are derived from the rule set — so the deterministic
   arms hold a structural advantage there, which the report marks.
6. **Retire the second intake engine.** Two engines exist; one is dead code that
   still confuses the picture.
7. **Blind indexes**, if cohort search over encrypted columns is ever needed.
8. **Prove native tool calling beats the JSON planner**, or drop one of them.

---

*Every figure here is reproducible: `python -m app.eval.harness --self-check`,
`--arms A,A-full`, `--arms C-generic,C`. Patient data is entirely synthetic.*
