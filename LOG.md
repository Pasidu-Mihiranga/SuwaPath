# SuwaPath — development log

A handover document. `README.md` tells you what SuwaPath *is* and how to run
it; this file tells you **how it got here, what broke, and why things are the
way they are** — the context that is expensive to rediscover and invisible in
the final code.

If you are picking this project up cold, read [Decisions worth
knowing](#decisions-worth-knowing) and [Bugs and their root
causes](#bugs-and-their-root-causes) first. Those two sections are the ones
that will save you time.

**Provenance.** The timeline is reconstructed from `git log` and the working
session transcripts. Commits are real; timestamps are real. Where a detail was
not recoverable it is marked rather than guessed.

---

## Contents

- [Where it started](#where-it-started)
- [Timeline](#timeline)
- [Architecture as it stands](#architecture-as-it-stands)
- [Decisions worth knowing](#decisions-worth-knowing)
- [Bugs and their root causes](#bugs-and-their-root-causes)
- [What is not done](#what-is-not-done)
- [Conventions](#conventions)

---

## Where it started

The brief: an AI patient-navigation, clinical-intake and hospital-intelligence
platform for Sri Lanka, for **AI Buildathon 2026**, team **Gmora**, SDG 3.

The framing constraint from the first prompt, which shaped everything after it:

> This must be a FUNCTIONAL PRODUCT, not a UI-only demo.

Original scope: 5 roles (patient, guardian, doctor, hospital admin, system
admin), symptom conversation in English/Sinhala/Tamil, a deterministic
red-flag engine, capability-aware provider matching, OCR document
intelligence, CV image screening, care programmes, no-show prediction, demand
forecasting, and 7 end-to-end demo scenarios.

Requirements added later, in the order they arrived:

| Ask | Where it landed |
| --- | --- |
| Use LangGraph | `app/agent/graph.py`, `app/services/graph.py` |
| Refer to `Ui mocks/`, responsive desktop + mobile, don't copy the crowded layouts | Whole frontend |
| Real logos from `Logos/` | `frontend/public/brand/` |
| **Never emojis** — use icons | `components/Icon.tsx`, 88 replaced |
| Centralised style file, no inline-CSS hassle | `styles/tokens.css` |
| Proper README, cross-platform, no Docker locally, Mermaid diagrams | `README.md` |
| Free tier only — no pay-as-you-go anywhere | Multi-provider failover |
| Scrape/synthesise a knowledge base into a vector DB, no hardcoding | `app/knowledge/providers.py` — generated from live DB rows |
| Guardrails and LLM-as-judge | `app/agent/guardrails.py` |
| **Privacy** — "is PII masking enough? I don't think so" | `app/privacy/boundary.py` |
| Unify symptom checker + assistant + STD advisor into one ChatGPT-like chat | `app/agent/`, `pages/patient/Assistant.tsx` |
| Ask follow-up questions dynamically, reason backwards, like a doctor | `app/agent/consult.py` |
| Private mode, resumable with a 6-digit PIN, nothing stored | `app/models/chat.py`, `app/services/chat_store.py` |
| Suggest doctors/labs as a swipeable card deck, like the AgentKap reference | `components/ProviderDeck.tsx` |
| Book, upload and ask — all from inside the chat | `pages/patient/Assistant.tsx` |
| Remember the patient across conversations | `app/models/memory.py`, `app/services/memory.py` |

The privacy question was flagged by the user as the single biggest concern,
and it is the one the architecture is most obviously bent around.

---

## Timeline

### Phase 1 — Foundation (`cd41f7c` … `9ffa6d3`, 2026-08-07)

Thirteen commits landing the whole skeleton: config and 43-table schema,
clinical engines, 100+ REST routes, seeder, React app, component library,
pages, AI orchestration.

Choices made here that still hold: PostgreSQL on port **5436** (a local
Homebrew quirk, not a standard), embedded on-disk Qdrant so there is no
container to run, and deterministic engines owning every clinically decisive
output.

### Phase 2 — Design system (`9d9825a`, `00cc2b5`)

Centralised tokens. The demo-user list was removed from the login page — the
user's words were "what a shame" — and replaced with seeded accounts
documented in the README instead.

`00cc2b5` is the fix for the transparent-modal bug. See
[Bugs](#1-tailwind-could-not-put-alpha-into-a-colour-token).

### Phase 3 — Gaps found by using it (`5e9ad96`)

Three defects the user hit by clicking around: no profile/settings pages per
role, patients could not join care programmes, and no in-app route into
confidential mode.

### Phase 4 — Agent graph (`77d3f08`, `f3dae54`, `9959dae`)

Parallel fan-out with `Send()`, guardrails on both sides, the PHI boundary,
and SSE streaming of real node events. 19 checks in `tests/test_agent.py`.

Adapted from the user's own `aee-capstone` hospital chatbot — specifically its
CAG idea and the status-chip chat UI.

### Phase 5 — The rewrite (`9f6d87a`, `00e1667`, `cb6eefe`, 2026-08-08)

Triggered by the user seeing the assistant in use and reporting, accurately,
that every answer was a database dump, the symptom checker was useless, and
three separate screens should have been one.

The diagnosis turned out to be a single root cause with wide blast radius —
see [the Gemini quota
finding](#2-every-answer-was-a-database-dump--gemini-quota-was-zero).

What shipped: multi-provider LLM router, doctor-style consultation, CAG,
chat persistence, private mode, guarded web search, markdown rendering,
route pruning, and a cross-platform README with Mermaid diagrams.

**Current state:** `tests/test_agent.py` 19/19, `tests/test_scenarios.py`
73/73, `tsc --noEmit` clean.

### Phase 6 — Closing the gaps (2026-08-08)

Everything the previous phase listed as "not done", plus a provider carousel
modelled on an e-commerce reference the user supplied.

- **Knowledge ingestion** became real: three Qdrant collections, and the
  provider directory is now *generated from live database rows* rather than
  hand-written. 123 documents from the seeded data.
- **Provider deck** — a fanned card carousel for suggesting doctors,
  facilities and tests, pickable from the conversation.
- **In-chat actions** — booking sheet and report/scan upload, without leaving
  the chat.
- **Memory tiers** — durable facts on PostgreSQL, with provenance and
  patient-facing delete.
- **Voice** — Web Speech dictation and read-aloud, degrading to nothing where
  unsupported.
- **Crisis titling** — a crisis disclosure no longer becomes a history heading.

Two availability bugs found and fixed on the way; see
[Bugs](#11-three-code-paths-disagreed-about-what-a-free-slot-is).

**Current state:** `test_agent.py` 19/19, `test_scenarios.py` 73/73, a new
16-check feature suite, `tsc --noEmit` and `vite build` clean.

### Phase 7 — Interface polish and encryption at rest (2026-08-09)

Driven almost entirely by the user looking at real screens and saying what was
wrong. Most of the UI defects in this phase were introduced by the same
session that fixed them; they are recorded because the causes repeat.

- **Illustrations were invisible.** The source art was framed cards with an
  opaque pale background; laid over a pale gradient they read as nothing. Cut
  to transparency with a border-seeded flood fill — see
  [Bugs](#13-the-illustration-that-was-rendering-all-along).
- **Programme artwork was never wired up.** `programmeIllustration()` existed,
  was correct, and was never called.
- **Gender-aware artwork** now has a source: an optional Sex field on the
  account page, plus `sex` on `PATCH /auth/me`. Before this the only value
  came from the seeder, so the feature worked in the demo and was inert for
  real accounts. Unset clears the stored value — a field about identity has to
  be retractable.
- **Encryption at rest** — AES-256-GCM over conversation content, PIN-derived
  keys for private transcripts, 90-day retention. See
  [Decisions](#why-private-mode-moved-from-storing-nothing-to-pin-derived-encryption).
- **Private chat became reachable.** The resume endpoint had existed and was
  never called by any UI.
- **Assistant layout** — mode toggle moved into the composer, history moved to
  a frosted panel behind a corner button.
- **Full-width buttons removed** across nine call sites; `sp-btn-block` is now
  the only way to opt in, documented as narrow-container-only.

**Current state:** `test_scenarios.py` 73/73, `tsc --noEmit` and `vite build`
clean.

---

## Architecture as it stands

```
backend/app/
  agent/        2,900 LOC   the assistant
    graph.py                LangGraph: guard → cache → route → fan-out → merge → judge
    consult.py              doctor-style history taking + backward reasoning
    cag.py                  reviewed-answer cache + per-session memory
    guardrails.py           deterministic input screen + output judge
    tools.py                the only way an agent touches data; consent enforced here
    websearch.py            Tavily, domain-ranked, personal queries refused
    state.py                AgentState + the operator.add reducer
  knowledge/                retrieval corpus and ingestion
    providers.py            directory prose GENERATED from live DB rows
    policy.py               how SuwaPath itself works
    chunking.py             sentence-aware overlapping passages
    ingest.py               `python -m app.knowledge.ingest --probe`
  privacy/        330 LOC   boundary.py — minimise, pseudonymise, guard, rehydrate
  clinical/     1,200 LOC   lexicon (~90 concepts × 4 scripts), 24 red-flag rules
  services/     5,400 LOC   red_flag_engine, navigation, matching, ocr, vision,
                            analytics, knowledge, llm, chat_store, availability
  api/          7,100 LOC   108 routes
  models/       1,900 LOC   43 tables
  seed/         2,800 LOC   synthetic Sri Lankan dataset

frontend/src/
  pages/          13 pages across 5 roles
  components/     ui.tsx, Icon.tsx, Markdown.tsx, AppShell.tsx
  styles/         tokens.css — single source of truth
```

### The load-bearing invariants

Break any of these and the product stops being defensible. They are asserted
in tests, not just documented.

1. **Urgency is deterministic.** `red_flag_engine` decides it from the
   patient's own words, re-deriving concepts rather than trusting a model's
   symptom list. The output judge may soften or block; it may **never**
   escalate. If it could, a prompt injection would be a way to manufacture
   emergencies.

2. **Consent is enforced in the SQL, not the prompt.** `tools.py` builds
   queries scoped by the guardian's granted permissions. A model that is never
   handed the data cannot be argued into revealing it.

3. **Four capabilities never leave the machine**: red-flag assessment, OCR,
   image analysis, and anything in a confidential session
   (`LOCAL_ONLY_CAPABILITIES`).

4. **Every route has a field allowlist.** Adding a route without adding its
   entry in `ROUTE_FIELDS` silently gives it the most restrictive `direct`
   set — fail-closed by design.

5. **Nothing clinical is ever cached.** Two patients asking "is this chest
   pain serious?" produce near-identical embeddings and completely different
   correct answers. Enforced by `_CACHEABLE_KINDS`, not by a similarity
   threshold.

6. **The product works with zero API keys.** Deterministic composers write
   structured markdown from the engines' own output. A quota outage degrades
   wording, nothing else.

---

## Decisions worth knowing

### Why three LLM providers instead of one

No free tier is reliable enough to build on. Groq → OpenRouter → Gemini, each
independently allowed to fail, 60-second cooldown on failure.

Measured on 2026-08-08:

| Provider | Model | Latency | Note |
| --- | --- | --- | --- |
| Groq | `llama-3.3-70b-versatile` | 271 ms | primary |
| Groq | `llama-3.1-8b-instant` | 158 ms | routing/classification |
| OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | 1,301 ms | fallback |
| Gemini | `gemini-2.0-flash` | — | `limit: 0`, unusable |

Two provider quirks that will bite you if you forget them:

- **Groq rejects `response_format: json_object` unless the literal word
  "json" appears in the messages.** `llm.py` appends it automatically rather
  than making every caller remember.
- **Groq's `openai/gpt-oss-*` models return empty `content`** — they put
  everything in `reasoning`. Treated as a failure.
- **OpenRouter retires `:free` slugs constantly.** Of four obvious candidates
  tried, all four were gone. Check
  <https://openrouter.ai/models?max_price=0> before assuming a bad key.

### Why the fallback router detects multiple intents

Parallel fan-out is the headline capability. Making it depend on an external
quota would mean the marquee feature dies when a free tier does. The keyword
fallback splits clauses on `and`/`also`/`;`/`?`, so compound questions still
fan out with no API key at all.

### Why `judge` can never escalate

Stated above as an invariant; repeated here because it is the single most
likely thing for a future contributor to "improve". An LLM judge that can
raise urgency turns every prompt-injection payload into an emergency
generator. Escalation stays one-way and non-AI.

### Why private mode moved from storing nothing to PIN-derived encryption

**Superseded 2026-08-09.** The original design wrote no message bodies at all:
the transcript lived in a process-local buffer and the row held only an id, an
owner, a PIN verifier and an expiry. The reasoning was that the realistic
threat is a shared phone rather than a database attacker, and encryption at
rest does not help against someone holding an unlocked session.

That reasoning was sound and the implementation still failed, because
"process-local" was doing more work than anyone noticed. A restart, a deploy,
or simply a second Gunicorn worker answering the request lost the
conversation. Resume returned an empty transcript. The privacy property was
real; the feature was not.

Now the transcript is stored encrypted under a key derived from the PIN with
PBKDF2 and never written down. The server can read it only while someone who
knows the PIN is using it, so a database dump plus the entire application
configuration still yields nothing — and it survives a restart.

Losing the PIN still means losing the conversation.

### Why one private PIN per person, not per conversation

A per-session PIN cannot survive contact with resumption. Given only a PIN,
the server cannot tell which conversation was meant, so a wrong guess had to
count against every private session the user had — five mistyped digits could
lock out several unrelated conversations at once.

Requiring a session id alongside the PIN was the first attempt at a fix and
was worse: the id is not a secret, it identifies a row already scoped to the
caller, and asking a user to keep a UUID safe bought nothing while making the
feature unusable in practice.

One PIN per person removes the ambiguity. The PIN identifies the *person*; all
of that person's private sessions are then reachable.

That change forced a second one. Self-destruct after five wrong attempts made
sense when a PIN guarded one conversation — deleting it only cost an intruder.
Once a single PIN guards them all, self-destruction hands anyone holding the
phone a way to erase every private chat in five guesses. It now locks for 15
minutes instead.

### Why a hand-written Markdown renderer

`react-markdown` pulls in remark, micromark and a dozen transitive packages to
render text a language model produced. `components/Markdown.tsx` supports
exactly the syntax the assistant is instructed to emit, and everything is a
React text child — a model emitting `<script>` renders those characters and
nothing happens.

### Why `consult` does not get the knowledge tool

It has its own history-taking loop. Pulling corpus text into that prompt was
part of what made answers read as pasted reference material.

### Why routes get pruned after classification

Classifiers are generous. They return `direct` alongside `consult`, or
`records` because a reply mentioned "two days". Every extra route triggers a
merge, and merging an empty answer into a good one produces self-contradiction
— literally "your appointment is at 11:30… another source says the date is not
available". `_prune_routes` drops `direct` when anything substantive was also
picked, and keeps a mid-consultation reply on `consult` alone.

---

## Bugs and their root causes

The root causes matter more than the fixes. Several of these are classes of
bug that will recur.

### 1. Tailwind could not put alpha into a colour token

**Symptom:** the book-appointment modal backdrop was fully transparent.

**Cause:** `bg-ink-900/50` compiled to `rgba(0,0,0,0)`. Tailwind cannot inject
an alpha channel into `var(--sp-ink-900)` when the variable holds a complete
`rgb(...)` string.

**Fix:** every colour is defined twice — an RGB triplet and a composed colour —
and the Tailwind config maps to `rgb(var(--sp-X-rgb) / <alpha-value>)`.

```css
--sp-ink-900-rgb: 10 46 86;
--sp-ink-900: rgb(var(--sp-ink-900-rgb));
```

Verified in the live DOM before and after: `rgba(0,0,0,0)` → `rgba(10,46,86,0.5)`.

**Second cause, same symptom:** the modal panel used the class `card`, renamed
to `sp-card` during the token migration. Two independent bugs presenting
identically — worth remembering when one fix only half-works.

> **Watch out:** scripting that migration with a regex also rewrote
> `fontFamily`, `borderRadius`, `boxShadow` and `spacing` into `rgb(...)`.
> Reverted by hand. Non-colour scales stay plain `var(--sp-X)`.

### 2. Every answer was a database dump — Gemini quota was zero

**Symptom:** every reply, regardless of question, opened with
`Current recommendation: respiratory medicine, urgency urgent… [kb-sym-006] Abdominal pain…`

**Cause, in two layers:**

- Gemini returned `429 … limit: 0`. Not exhausted — **never granted**. A free
  key that authenticates but has no quota allocated.
- With no model, `_synthesise` fell back to `"\n".join(tool_texts)`, i.e. raw
  tool output. Every route, every question.

**Fix:** the multi-provider router (Groq works), *and* deterministic composers
that write structured markdown from the engines' own output. The second half
matters more — the fallback should be usable, not a placeholder.

**Lesson:** a `429` with `limit: 0` is a provisioning problem, not a rate
limit. Retrying and waiting will never help.

### 3. Groq looked dead but was not

**Symptom:** `403` / `error code: 1010` from Groq.

**Cause:** Cloudflare blocking `urllib`'s TLS fingerprint. Nothing to do with
the key. The same request through `httpx` worked first time.

**Lesson:** before concluding a key is bad, try a real HTTP client.

### 4. Enum `is` versus `==` — three separate occurrences

**Symptom:** `programme.programme_type is ProgrammeType.ELDERLY` never fired.

**Cause:** SQLAlchemy returns plain strings for these columns, not enum
members. Identity comparison always fails.

**Fix:** 42 occurrences converted across 13 files. **This is the most repeated
bug class in the codebase — check for it in any new model code.**

### 5. SSE stream stalled after the first agent — three sequential causes

Each fix revealed the next:

1. `asyncio.Queue.put_nowait()` called from a worker thread. Not thread-safe.
   → `loop.call_soon_threadsafe`.
2. `This session is provisioning a new connection; concurrent operations are
   not permitted` — LangGraph runs fan-out branches on separate threads
   sharing one SQLAlchemy `Session`. → per-node `agent_session()`
   contextmanager.
3. Still hung (`curl_exit=28`). → replaced the hand-rolled thread+queue bridge
   entirely with native `agent_graph.astream()`.

**Lesson:** do not bridge a sync generator into asyncio by hand when the
library ships an async interface.

### 6. Booked appointments collided with the slot that was offered

**Symptom:** `next_available` returned a slot; booking it returned
`409 That slot has just been taken.`

**Cause:** slots are generated from `schedule.slot_duration_minutes` (15 min),
but the booking endpoint hard-coded `duration_minutes or 20`. A 15-minute slot
became a 20-minute appointment overlapping the next slot — and if *that* one
was booked, the conflict check fired against a slot the patient had just been
offered.

**Fix:** `slot_duration_for(doctor, start)` derives the length from the
doctor's own schedule.

This was the one failing check in `test_scenarios.py` (72/73 → 73/73).

### 7. Vector search always returned its top-k

**Symptom:** "Who is Kusal Mendis" produced a health answer citing STI testing
and skin lesions.

**Cause:** `build_context` returned the three least-unrelated corpus documents
however poor the match, and the model wrote them up.

**Fix:** `MIN_RELEVANCE = 0.35`. Below it, the tool returns an explicit
"nothing relevant found" instruction so the agent says it does not know.

### 8. Guardrail gaps found by adversarial testing

- "how to inject the paracetamol using a syringe" — passed. The dosage rule
  only matched "how many mg should I", not route of administration. Added
  `administration_route` and `procedure_at_home`.
- Pretext framings ("hypothetically, as a doctor you would…") — passed. Added
  `pretext` and `safety_override`.

**Lesson:** guardrail regexes need adversarial testing, not example-driven
testing. Write the attack first.

### 9. Web-search filters leaked on first pass

Two independent misses:

- `"my blood test results"` was not caught, because `\bmy\s+(?:test|result)`
  requires adjacency. Fixed with `\bmy\b(?:\W+\w+){0,3}\W+`.
- NHS dosing text survived (`"Leave at least 4 hours between doses"`) because
  `\bdose\b` does not match `doses`, and `"2 extra tablets"` puts a word
  between the number and the unit.

Both are the same underlying mistake: **regex written against the example in
front of you rather than the pattern**.

### 11. Three code paths disagreed about what a free slot is

**Symptom:** a doctor's card advertised a next-available slot while the new
availability endpoint returned zero slots for the same doctor.

**Cause:** `generate_slots_for_doctor` enforced `schedule.max_patients`;
`next_available_map` did not. Where a clinic block was longer than
`max_patients × slot_duration` and the early slots were booked, one function
saw nothing free and the other happily offered a slot past the cap.

Worse, `slot_matches_schedule` — the booking guard — only checked the block
window, so a slot beyond the doctor's stated daily limit would actually book.

**Fix:** extracted `_slots_in_block` as the single definition of "is this slot
bookable", used by both enumerators, and taught `slot_matches_schedule` to
enforce the cap by slot position.

**This is the same class as bug 6, and that is the point.** Availability was
computed in more than one place, so the copies drifted. Patching the symptom
again would have guaranteed a third occurrence. If you add another
availability caller, route it through `_slots_in_block`.

### 12. "Where can I get an MRI" returned the patient's old blood count

**Symptom:** a question about where to obtain a test retrieved the patient's
existing records instead.

**Cause:** "where can I get an MRI" and "what did my MRI show" share every
keyword. The classifier picked `records` for both, and the merge led with it.

**Fix:** `_SEEKING_SERVICE` detects the question *form* — where / how much /
cheapest / nearest, near a test noun — and drops `records` when `admin` is
also present. The classifier does not get a vote on this one.

### 13. The illustration that was rendering all along

Reported as "no image in the welcome card". The data was correct, the `<img>`
was in the DOM, the file returned 200. The asset itself was the bug: framed
card art with an opaque near-white background, drawn over a near-white
gradient card.

Cutting it to transparency took three attempts, and the failures are the
useful part:

1. **Flood fill from the corners** — the rounded frame is a closed stroke, so
   the fill stopped at it and left the interior untouched.
2. **Seed deeper to get past the frame** — this silently *destroyed* artwork.
   Inset seeds landed on white hair and dark trousers and ate the figures from
   the inside. Nothing in the output looked wrong until every asset was
   composited over a contrasting colour and inspected.
3. **Crop the frame off first, then fill from the true border** — no seed ever
   touches the picture.

The threshold mattered as much as the seeding: the background is
`(230,238,235)` and the nearest artwork colour is white hair about 36 away, so
a tolerance of 46 reached past the background into the hair. 24 clears
compression noise and stops short.

**Lesson:** an image pipeline needs a proof sheet. A batch job that reports
"cut 72%" tells you nothing about whether it removed the right 72%.

### 14. Encryption silently did nothing

`crypto.py` read its key with `os.getenv("SUWAPATH_ENCRYPTION_KEY")`. The
project keeps configuration in `.env` loaded by pydantic-settings, which
populates a `Settings` object and **never exports to the process
environment**. So the key was always absent, `encrypt()` returned its input
unchanged by design, and everything appeared to work — messages saved, history
loaded, tests passed.

It was caught by querying the database directly and reading the raw column,
which showed plaintext. Now read through `settings`, with `os.getenv` only as
a fallback.

**Lesson:** verify security features at the layer they claim to protect. An
encryption feature that is tested through its own API tests nothing — both
sides of the round trip agree whether or not the key exists.

### 10. Smaller ones

| Bug | Cause |
| --- | --- |
| `websockets` version conflict | langgraph wants ≥15, google-genai wants <15 → pinned `14.2` |
| `.venv/bin/python: no such file` | venv is at `backend/.venv`; user ran from repo root. README callout added. |
| Stuck care-programme enrolment | `CareEnrollment` without a backing `ElderlyRecord`; re-enrol 409'd, dashboard 404'd. Made idempotent and self-healing. |
| `Notice` has no `className` prop | Wrapped call sites in a `div`. |
| Card/Stat text clipped | `truncate` → wrap (`leading-snug`, `break-words`). |
| ~60 raw Tailwind palette colours | Bypassed the token system across 11 files. Converted; also deleted a duplicate `TONE` map in `Guardian.tsx`. |
| Arrow glyphs survived the emoji sweep | `←`/`→` in 6 files → `Icon` components. |
| `status_code=204` + return annotation | FastAPI asserts. Returns a body with 200 instead. |
| Node name `cag` collided with state key `cag` | LangGraph rejects it. Node renamed `cache_lookup`. |
| `aee-capstone/.git` nested repo | Added to `.gitignore` rather than committing a broken gitlink. |

---

## What is not done

### Known gaps in encryption

- **Only conversation content is encrypted.** `ChatMessage.meta`, report OCR
  text, and the clinical tables are still plaintext. Conversations were done
  first because they carry the most volunteered detail, not because the rest
  is safe.
- **No key rotation path.** The ciphertext carries a `v1.` version prefix so
  rotation is possible, but nothing re-encrypts existing rows. Changing
  `SUWAPATH_ENCRYPTION_KEY` today makes old conversations unreadable.
- **The key sits in `.env`.** Fine for this deployment, not for production —
  a KMS or Vault holding the master key, with per-record data keys, is the
  pattern that gives rotation and an audit trail of every decrypt.
- **Retention runs opportunistically**, on the same request that purges
  expired private sessions. A conversation older than 90 days survives until
  someone opens their history. It wants a scheduled job.

### Worth doing next

- **Reindex on write.** `python -m app.knowledge.ingest` must be run manually
  after an admin adds a doctor. It should be triggered automatically on
  provider mutations, or on a schedule.
- **Memory in the UI.** `GET/DELETE /agent/memory` exist and are tested, but
  nothing in the frontend surfaces them. A memory store the patient cannot see
  in the product is only half-honest — the endpoints were built first
  deliberately, but the screen is missing.
- **Deck actions beyond booking.** Picking a facility or a test asks a
  follow-up question. Directions, calling the facility, and adding a test to a
  care plan are not wired.
- **LiveKit.** `lib/voice.ts` is structured so only `listen()` would change,
  but the LiveKit path itself is not written. Web Speech covers dictation
  today; its Sinhala and Tamil accuracy is noticeably worse than English.

### Medium

- **Sinhala/Tamil assistant output** has not been reviewed by a speaker. The
  clinical lexicon is genuinely trilingual and language is passed into every
  prompt, but nobody has checked whether the consultation *reads* well.
- **Bundle size.** 875 kB before gzip, one chunk. Route-level code splitting
  is the obvious fix.
- **CV model.** `BaselinePneumoniaAdapter` is a transparent *untrained*
  heuristic. Drop a trained model at `models/pneumonia/*.onnx` and the ONNX
  adapter takes over automatically.
- **Memory decay.** `confidence` is stored and context facts expire, but
  nothing decays confidence over time as designed.

### Before this could touch real patients

- **Free model tiers may train on submitted content.** That makes them
  unsuitable for real PHI regardless of the boundary quality. Every record in
  the repo is synthetic, and `/api/v1/agent/status` reports
  `safe_for_real_phi: false` rather than hiding it. Production needs a paid
  zero-retention endpoint or a self-hosted model. **The boundary code does not
  change; only the egress destination does.**
- Clinical review of the 24 red-flag rules by an actual clinician.
- `JWT_SECRET` is a dev value.
- Dockerisation for deployment (deliberately not used for local development).

---

## Conventions

### Commits

Per the user's explicit instruction:

- Username **`Oxshadha`**
- **No `Co-Authored-By` lines, ever**
- **Short messages** — one line, conventional-commit prefix

```bash
git -c user.name=Oxshadha commit -m "feat(agent): add web search tool"
```

### Code

- **No emojis anywhere.** Icons come from `components/Icon.tsx`.
- **No inline styles and no raw Tailwind palette colours.** Everything routes
  through `styles/tokens.css`. If a colour is missing, add a token.
- Comments explain *why*, not *what*. The codebase already reads this way;
  match it.
- Deterministic fallback for every model call. No exceptions.

### Documentation

`README.md` and this log have different readers, and mixing them is the easy
mistake to make.

**README is written for someone who has just found the project** and wants to
know what it is, whether it is worth their time, and how to run it. It is
descriptive, in the present tense, and about the software as it stands:

- Say what a thing *is*, not why a choice was defended. "Private transcripts
  are encrypted under a PIN-derived key" — not "we thought about this
  carefully and here is why it is not a weakness."
- No self-assessment, no scoring, no arguing with an imagined critic.
  Headings like *"Is X enough?"* or *"What is real and what is not"* are a
  conversation with a reviewer, not documentation. Use *"How the PHI boundary
  works"*, *"Implementation status"*.
- No debugging narrative. "You ran the command from the wrong directory"
  becomes "Run this from `backend/`." State the fix; skip the diagnosis of the
  reader.
- Limitations belong there, stated plainly and once — a *Synthetic data only*
  section, a placeholder marked in a status table. Plain limits build more
  trust than paragraphs defending them.
- Structure follows the convention readers expect: what it does → highlights →
  architecture → getting started → configuration → API → testing → limitations
  → troubleshooting. Someone should be able to run it from the README alone.

**This log is for whoever maintains it next.** Reasoning, rejected
alternatives, bugs and their root causes belong here — the expensive context
that would otherwise have to be rediscovered. When a README section starts
explaining *why*, it usually belongs in this file instead.

### Testing

Both suites run against a live API, not a test client:

```bash
cd backend
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000   # in one shell
PYTHONPATH=. .venv/bin/python tests/test_scenarios.py     # 73 checks
PYTHONPATH=. .venv/bin/python tests/test_agent.py         # 19 checks
```

`test_agent.py` asserts *properties* — consent enforcement, guardrail
behaviour, that urgency stays deterministic — rather than the wording of any
answer. Keep it that way; wording changes with the provider.

Frontend: `cd frontend && npx tsc --noEmit`.

### Local environment

- PostgreSQL on **5436** (a Homebrew quirk of this machine, not a standard).
  `brew services start postgresql@16`.
- No Docker for local development. Qdrant is embedded and on-disk.
- Run backend commands from **inside `backend/`** — the venv is at
  `backend/.venv`.
