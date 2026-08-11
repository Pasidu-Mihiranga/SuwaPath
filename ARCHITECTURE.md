# How SuwaPath actually works

Written to answer one objection directly, because it is the objection a
reviewer will raise and the demo script did not handle it well:

> A rule-based chatbot has no place in agentic AI.

That is correct, and it is not what this is. But the distinction only lands if
you can say *exactly* which decisions are made by rules, which are made by a
model, and why that split is the right one rather than a compromise. This
document does that, then explains the loop, the graph and the autonomy layer
in enough detail to defend them.

---

## 1 · The objection, answered

### The two things being confused

A **rule-based chatbot** maps input to canned output. `if "fever" in text:
reply(FEVER_SCRIPT)`. It has no goal, no state beyond the current turn, and no
ability to do anything it was not explicitly scripted for. That is a dead
architecture and nobody should defend it.

A **deterministic classifier inside an agentic system** is a different object.
It takes a concept set and produces a *label* — here, an urgency level plus a
list of required facility capabilities and specialty hints. That label is not
an answer to the user. It is **input to the agent's planning**, and it
constrains the action space the agent may then move in.

The first replaces the agent. The second feeds it. SuwaPath does the second.

### What is rules, precisely

Exactly one thing: **how urgent is this, and what kind of facility does it
need.** 23 rules in `app/clinical/red_flag_rules.py`, matched against a
concept lexicon in `app/clinical/lexicon.py`.

```
RF-MAT   ×5    RF-NEURO ×4    RF-RESP  ×3    RF-CARD ×3
RF-GI    ×2    RF-GER   ×2    RF-PAED  ×1    RF-MH   ×1
RF-INF   ×1    RF-BLEED ×1
```

`_resolve_urgency` takes the **highest** urgency among triggered rules. No
model is consulted, and no model output can raise or lower it.

### What is not rules

Everything else:

- **Which route the question takes** — a model classifies intent, with a
  keyword fallback.
- **Which tools to call, in what order, and when to stop** — the ReAct
  planner, per turn, from what it has already learned.
- **Whether the answer is good enough** — a judge node that can send it back
  for revision.
- **What to say** — synthesis, under a route's field allowlist.
- **What to notice when nobody asked** — eight detectors over live state.
- **What to propose, to whom, and at what risk tier** — the action ladder.

### Why the split is right, not a compromise

Three arguments, in ascending order of how much they matter.

**One: agency without constraint is not more agentic, it is unaccountable.**
The interesting property of an agent is that it chooses actions you did not
enumerate. In a medical context, the set of actions it must *never* choose
also has to be enumerable, or you cannot ship it. Constraining the action
space is what makes the agency deployable. Every serious agentic deployment
does this; the alternative is a system whose worst case nobody can state.

**Two: the failure mode you are protecting against is real and specific.** If
an LLM sets urgency, then a prompt injection, a bad sampling seed, or a
provider outage returning garbage can downgrade an emergency to self-care.
There is no test that closes that hole, because the input space is infinite.
Moving urgency to 23 auditable rules makes the worst case *statable*: the
system can be wrong about urgency only in ways a clinician can read in the
rule table and correct.

**Three — the one that actually answers the objection: remove either half and
see what survives.**

Remove the rules: the agent still routes, plans, calls tools, loops, judges,
revises, detects and proposes. It is fully agentic and unsafe.

Remove the model: `DeterministicPlanner` reproduces the old fixed handoffs, so
the system still *functions* — but it stops choosing. No routing by meaning,
no tool selection, no stopping decision, no synthesis, no revision.

**The agency lives in the model half. The rules constrain it.** That
asymmetry is the answer. If the rules were the agent, removing the model would
change nothing.

### How to say this on camera

Do not open with "urgency is deterministic". That framing invites exactly the
objection above, because it leads with the least agentic component.

Lead with the loop — *it decides what to look up, and when it has enough* —
show a multi-step trace, and introduce determinism afterwards as the
**boundary the loop cannot cross**:

> The agent chooses what to do. There is exactly one thing it is not allowed
> to choose, and that is how urgent you are.

Same fact, correct emphasis.

---

## 2 · The request lifecycle

A message enters a LangGraph state machine defined at `app/agent/graph.py:1035`.

```
START
  ↓
guard_input          input guardrails; can terminate here
  ↓
cache_lookup         cache-augmented generation; can short-circuit
  ↓
route                intent → one or more agent branches
  ↓
  ├── consult_agent      ┐
  ├── admin_agent        │
  ├── records_agent      │  dispatched in parallel,
  ├── knowledge_agent    │  none can see another's output
  ├── web_agent          │
  └── direct_agent       ┘
  ↓
merge
  ↓
fulfil               ← the ReAct loop lives here
  ↓
judge  ⇄  revise     judge may send the answer back
  ↓
END
```

### Why fan-out alone was not enough

Parallel dispatch is **parallel, not collaborative**. Every branch is chosen
at routing time from the same decision, and none can see what another
produced. That is correct for a question with independent parts ("my
appointment *and* my blood test") and useless when one agent's output is
another's input.

The concrete failure, quoted from the code: a consultation concludes "see a
gastroenterologist, and an abdominal ultrasound would settle it" — and stops.
The admin agent could have found that gastroenterologist and priced that
ultrasound. It was never dispatched, because **at routing time nobody knew a
gastroenterologist would be the answer.**

That is what the loop exists to fix.

### `judge → revise` is a real cycle

`judge` has three outcomes: pass, **REGENERATE**, or a terminal block. The
regenerate path returns to `revise`, which returns to `judge`. This is a true
cycle in the graph, not a retry wrapper.

It also shipped an infinite loop: the terminal path did not clear
`judge_constraints`, so the `judge → revise` edge fired forever until
LangGraph's recursion limit. Caught by an end-to-end call returning an empty
answer — **not** by a test. There is now a termination test.

---

## 3 · The ReAct loop

`app/agent/react.py`, 389 lines. Called from `fulfil_node`.

### The cycle

```
                ┌──────────────────────────────────┐
                ▼                                  │
   plan ──▶ validate ──▶ execute ──▶ observe ──────┘
    │                                    │
    │  finish                            │  metadata only
    ▼                                    ▼
  synthesis  ◀────────────────────  scratchpad
```

**plan** — the planner returns JSON:

```json
{"thought": "...", "action": "call_tool", "tool": "find_care", "args": {...}}
```

or `{"action": "finish"}`. `llm.py` has no native tool-calling, so the
protocol is plain JSON at `temperature=0.0`.

**validate** — the named tool must be in `allowed` *and* in `TOOLS`. A tool
outside the caller's consent scope was never in the manifest, so it cannot be
named. Then the repeat check.

**execute** — `TOOLS[tool](db, scope, **args)`. Four failure modes are caught
separately and each becomes an observation rather than ending the turn:
`ToolDenied` → `denied`, `TypeError` → `bad arguments`, any other exception →
`error`.

**observe** — an `Observation` is appended. This is the important part.

### The planner never sees content

```python
def for_planner(self) -> dict:
    """Strictly metadata. No field of any record ever appears here."""
    return {"step": ..., "tool": ..., "status": ..., "n_results": ...}
```

The planner sees `{"step": 2, "tool": "find_care", "status": "ok",
"n_results": 3}`. It never sees which doctors.

Payloads go to `pad.payloads`, a separate dict the planner has no access to,
merged into the answer at the end.

**Why this is not just tidiness.** The PHI boundary minimises fields *per
route*, and `web` — the one route whose payload permanently leaves the
infrastructure — has the tightest allowlist. A scratchpad accumulating record
values across steps is a route-crossing leak waiting for the planner prompt to
include it.

Because content never enters the planning prompt, that leak is **structurally
impossible rather than forbidden**. Content is read exactly once, by
synthesis, under a single route's allowlist. This is the strongest safety
property in the codebase and it is a *design* property, not a rule.

### The bounds, and the reason for each

| Bound | Value | Why |
|---|---|---|
| `MAX_STEPS` | 4 | consult → find_care → directory, with one spare |
| `DEADLINE_MS` | 12,000 | Groq answers under 1s; a free OpenRouter model routinely takes 10. Three sequential calls on a slow provider blows any conversational budget |
| `MAX_TOOL_CALLS` | 6 | backstop independent of steps |
| repeat detector | `sha256(tool + canonical args)[:16]` | the classic loop failure |

The repeat detector **refuses with an observation** — `"already called with
these arguments"` — rather than silently re-running. Telling the planner it
repeated itself is information; silently re-running is not.

### Scope filtering happens before the manifest

```python
def allowed_tools(scope: dict) -> list[str]:
```

A guardian without the `medications` consent scope does not get that tool
filtered out at call time — it is **never in the list of tools the planner is
shown**. Visibility, not just enforcement. No prompt can talk the planner into
naming a tool it was never offered.

### Tool purposes exist because of an observed failure

`TOOL_PURPOSE` gives each tool a one-line description. Without it the planner
picked by name alone and reached for whatever sounded relevant — in practice,
calling the knowledge base repeatedly with slightly different phrasings.

### The loop is gated, deliberately

It runs only when `consult.mode` is `assess` or `escalate` **and** there is a
specialty or a suggested test. From the source:

> The loop exists to *fulfil* — to turn a conclusion into something the
> patient can act on. With no conclusion there is nothing to fulfil, and
> running it anyway costs a model call plus several lookups on every message
> and produces the aimless wandering that gives these loops a bad name.
> Observed before this gate: four knowledge lookups on a turn that needed
> none.

A confidential session skips it entirely: the loop can only read, but the
cheapest guarantee is not to run it at all.

**Be honest about this in a demo.** The loop does not run on every message.
That is a deliberate cost/benefit decision, not a limitation to hide — but
claiming "every turn is agentic" would be false.

### Zero-key degradation

With no provider, `DeterministicPlanner` reproduces the old `fulfil_node`
handoffs exactly: specialty → `find_care`, suggested tests → `directory`. The
graph degrades to what it did before rather than failing.

This is also the honest limit: **on the zero-key path there is no loop in any
meaningful sense** — it is a two-step fixed sequence wearing the loop's
interface. Say "with no API key it degrades to the previous fixed behaviour",
not "it still works the same".

---

## 4 · The autonomy layer

This is the part that answers "it's just a chatbot", because a chatbot cannot
do it: **nobody sent a message.**

### Two stages, and why the split matters

```
detector  ──enqueue──▶  agent_tasks  ──claim──▶  handler  ──▶  ActionProposal
(reads)                 (dedupe key)             (acts once)     (human decides)
```

**A detector reads and enqueues. It never acts.** It does not send, book,
alert, or write clinical state. It notices that a care journey has stalled and
puts a task on the queue with a dedupe key describing *what it noticed*.

That split is what makes running these every few minutes safe: **a detector
firing a thousand times produces one task**, because the database rejects the
duplicate key. Nothing depends on the detector being careful.

Tasks are claimed with `FOR UPDATE SKIP LOCKED` under Postgres advisory locks,
so handlers run exactly once even with concurrent workers.

### The eight detectors

`referrals` · `medication` · `appointments` · `checkins` · `noshow` ·
`directory` · `followups` · `disengagement`

Listed explicitly in `__init__.py` rather than auto-discovered, so the set of
things the system does on its own is greppable in one place.

### The operational trap

The two stages are separate, and **only the first is visible**. A development
database was found holding 379 tasks in `queued` — 14 no-show batches, 64
lapsed follow-ups, 300 medication checks — with every Actions panel empty
except the patient's.

The work was real and recorded. It had simply never become a proposal. Run
`scripts/demo_prep` before showing anyone.

### The risk ladder

T0 / T1 / T2. Nothing auto-executes above T0. Proposals go to an
`ActionProposal` ledger rather than LangGraph's `interrupt()`, so a pending
decision survives a restart and is auditable.

**Authority is re-derived at execution and never inherited from the sender.**
A guardian-originated message to a doctor does not carry the guardian's
consent. Hop count is capped at 2.

**No natural language between agents.** Payloads are ids, enum codes and
numbers validated against a per-message-type allowlist; the receiving side
hydrates what it displays from the database under its own authority. Prose
between agents would re-introduce model egress at every hop and make the
receiver prompt-injectable by the sender.

### Cross-role, concretely

The doctor's *"2 follow-ups have lapsed — send a recall?"* is the proof. A
consultation recorded a follow-up date. The date passed. No appointment was
booked. A detector noticed and addressed a proposal to a **different role than
the one the data belongs to.**

---

## 5 · The honest weaknesses

Stating these yourself is worth more than being caught on them.

**The loop only runs after an assessment.** Most turns take the DAG path.

**Zero-key mode is not a loop.** It is the previous fixed behaviour.

**Only three of five roles have pending proposals** on demo data. Guardian and
system admin detectors need conditions the seed does not meet.

**`Forecast` is still write-only** — computed, stored, read by nothing.

**No evaluation harness for agent behaviour.** The suites assert properties —
consent enforcement, guardrail behaviour, PHI boundary, loop termination — not
answer quality. Nothing measures whether the planner picks *good* tools, only
that it cannot pick forbidden ones.

**No matcher impression log**, so ranking cannot be learned from outcomes yet.

**AUC 0.624** on no-show. Real, reproducible, and modest.

**The vision model is the untrained baseline.**

---

## 6 · One-paragraph version

> A message enters a LangGraph state machine. Guardrails, then cache, then a
> model routes it to parallel agents. Their outputs merge into a bounded
> reason-act-observe loop that decides for itself which internal lookups to
> run and when it has enough — seeing only whether each lookup returned
> anything, never what it returned, which is what makes a record-to-web leak
> structurally impossible rather than merely against the rules. A judge can
> send the answer back for revision. Separately and continuously, eight
> detectors read live state and enqueue what they notice; handlers turn those
> into proposals addressed to whichever role can act, which a human approves.
> Exactly one decision in the system is not the model's to make: how urgent
> you are. That comes from 23 auditable rules, and no model output can move
> it.
