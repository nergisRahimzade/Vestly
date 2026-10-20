Workpaper **VS-01** Event **CalHacks · Hyphenate track** Seats **Payroll accountant · Equity compensation accountant** Model **Claude Opus 5.5**

Plan of approach

# Vestly Payroll & Equity Agent

Two seats on the finance team of a 300-person software startup with a US parent and subsidiaries in the UK and Germany. Every month it reconciles the HR system, the payroll provider, the equity platform, the bank and the ledger, drafts the journal entries, catches the silent errors that build up between systems, asks the right human at the right moment, and records the full reasoning behind every call.

The brief, decoded

## What Hyphenate will actually score

The judging questions reward a narrow, deep system over a broad demo. Each of the five "hard parts" in their brief is a property judges will probe, so we design for all five and make each one visible on screen.

Sources disagree

### Know which one to trust, and say why

The HR system, payroll provider, equity platform, bank and ledger rarely agree. The agent ranks sources per field and states the ranking in every decision.

Time

### March changes June

Human rulings become precedents with an owner, a reason and a scope. A June payment is handled differently because of what the controller decided in March.

Working with others

### Ask at the right moment, handle the answer

An approvals inbox with deadlines and escalation. A "no" is a normal outcome the agent records, follows and cites later.

Limits

### Least privilege, enforced in code

The agent can draft entries and request funding but never approve or release them. The model cannot reach the approval path at all.

Accountable

### Every call traces back

Each action carries evidence: the document and quote, the rule, the person or the precedent behind it, frozen as it was on the day.

Judging map

## How each criterion shows up in the demo

| Criterion | What judges see | Built in tab |
| --- | --- | --- |
| **Is it real enough?** | Six months of messy books for a three-entity startup: HR, payroll, equity, bank and ledger exports, PDFs with OCR noise, eight planted anomalies and a scorer table run three times | Data & memory, Proof |
| **Would you hire it?** | Two seats (payroll and equity compensation) done completely; a policy file it can't break; an evidence trail per decision; questions asked only when needed | Controls, Agent & model |
| **Something new?** | Multi-entity payroll, four-year vesting, leavers and forfeitures, employer contributions, plus precedent memory and challenge mode | Overview, Data & memory |
| **The moment** | Opening 60 seconds: catches a fraudulent bank change by citing a March precedent, holds when the controller pushes, recovers a half-confirmed funding batch | Proof & demo |

Company, seat and sponsor fit

## Why payroll and equity

**Northlight Labs** (fictional) is a software startup: 300+ staff, a US parent and subsidiaries in the UK and Germany. Staff are paid through a payroll provider, tracked in an HR system and granted stock that vests over four years. Each system holds part of the truth. We fill two seats, the payroll accountant and the equity compensation accountant, and most of the monthly review between them.

Hyphenate already sells procure-to-pay for mostly software and technology customers: bills from the AP inbox, coded to GL, matched to POs, approvals by rules, vendors chased. A generic AP agent at a software company rebuilds their product. Payroll and equity accounting stays in their world (their customers are software companies) while adding work their generic flow does not cover:

- **Three entities, three payrolls**: US, UK and German cycles, currencies and statutory rules feeding one ledger.
- **Vesting** over four years: stock compensation expense recognized over the schedule, reversed when unvested grants are forfeited.
- **Joiners, leavers and comp changes** that must reach payroll and the equity platform in the same period, often approved after the fact.
- **Employer contributions**: 401(k) match, UK pension, German social contributions, each at its own rate.
- **Employee bank changes** by email, the classic route for payroll diversion fraud.

Call it "payroll and equity accounting" in the pitch, never "HR software", so no judge wonders whether it is on-track.

| Hyphenate feature (from their site) | Our payroll-and-equity extension |
| --- | --- |
| Bills matched to POs | Payroll registers matched to the HR system and approved comp changes; vest reports matched to grant terms and board approvals |
| Approvals routed on your rules | Separation of duties enforced in code, dual approval above a limit, call-back rule for employee bank changes |
| "Input needed" escalation | Approvals inbox where a "no" becomes a precedent later cases cite |
| Daily bank reconciliation | Bank as the authority for cash moved, reconciled to registers and ledger; idempotent re-send after a half-confirmed batch |
| Audit report | Per-decision evidence with document hashes, plus as-of replay and challenge mode |

System

## Four lanes, one evidence log

The model sits inside the agent core and never touches money directly. Every tool call passes the policy gate; entries post and funding goes out through the posting service and the adapter (dashed line) only after a human approval. Humans act through the web UI; precedents reach the model through a read tool.

Components

## What each part owns

| Component | Responsibility | Implementation |
| --- | --- | --- |
| Adapter seam | One interface for every source and action; swap mock for Hyphenate per method | Python `Protocol`, a mock and a Hyphenate class |
| Agent loop | Runs one case: model turns, tool calls, stops when the model stops calling tools | Anthropic Python SDK, manual loop |
| Tool registry | Tool definitions, input validation, which tools are gated | Pydantic models → JSON schema with `strict: true` |
| Deterministic services | Register match, contributions, SBC expense, duplicate keys, FX, bank reconciliation | Plain Python with `Decimal`, unit-tested |
| Policy gate | Allow or refuse each action by role, limits, separation of duties | `policy.yaml` + a small evaluator |
| Approvals inbox | Questions and approval requests with due dates and escalation | Table + UI view; identity from the login session |
| Posting service | Posts approved entries and releases funding once, confirms with bank and ledger | Idempotency key per entry or transfer; adapter call |
| Evidence log | Append-only record of every fact, call, decision and approval | SQLite table, rows never updated |
| Precedents | Human rulings with scope and expiry, retrieved for later cases | Table + structured match (employee, entity, rule) |
| Web UI | Inbox, case timeline, evidence drill-down, challenge box | React (Vite); Streamlit as fallback |

Request flow

## One case, start to finish

1. A payroll register or equity vest report arrives through the adapter. A **case** opens and the document is stored with its SHA-256 hash.
2. The model reads the document and returns its fields through **structured output**. Code checks every figure against the document text.
3. The model runs checks through tools: HR records, plan terms, grant approvals, ledger, bank, precedents. The arithmetic happens in deterministic services.
4. The model proposes an action (post, hold, reject, ask). The **policy gate** allows or refuses it; both are written to the evidence log.
5. When the model stops calling tools, **code decides the case outcome** from the recorded results, not from the model's final text.
6. Entries and funding wait in the **approvals inbox**. A human approves or declines in the UI. A decline becomes a precedent.
7. The posting service posts approved entries and releases funding with an idempotency key, the bank and ledger confirm, and the case closes reconciled.

Where the model fits

## Judgment to the model, bookkeeping to code

The fastest way to make an agent unreliable is to let the model do arithmetic, copy account numbers, or decide when something is finished. We split the work so each side does what it is good at.

### The model does

- Read unstructured documents: payroll registers, vest reports, employee emails
- Decide which checks a case needs and in what order
- Notice what is unusual and explain why
- Write the question to a human, with the right context
- Answer a challenge from the evidence record
- Propose the scope of a new precedent

### Code does

- All money math: matching, contributions, SBC expense, FX, reconciliation
- Duplicate detection on normalized keys
- Source ranking per field from a fixed table
- Allowing or refusing every action
- Deciding the case outcome from recorded results
- Posting and funding, once, with an idempotency key

The loop

## How one case runs

A refused action comes back to the model as a refusal it can explain, never as a silent failure. The loop has a round limit; hitting it makes the case "needs human", not "done".

Model configuration

## Settings and why

| Setting | Value | Reason |
| --- | --- | --- |
| Model | `claude-opus-5-5` | The team already uses it; one model means one prompt cache. Try a lower effort before adding a second model |
| Effort | `high` for case decisions, `low` for field extraction | Opus 5.5 defaults to `medium`, so set it explicitly per route |
| Thinking | Adaptive (default; it cannot be disabled on Opus 5.5) | We show our evidence record, not the model's reasoning, so the default display is fine |
| Tools | `strict: true`, `additionalProperties: false` | Tool inputs always match the schema |
| Tool choice | `auto` only | Forcing a specific tool returns an error on Opus 5.5; steer through the prompt |
| Parallel calls | Allowed; all results returned in one message | Splitting results across messages teaches the model to stop calling in parallel |
| Extraction | `messages.parse` with Pydantic, PDF as a document block | Validated fields in one call; we check quotes ourselves |
| Caching | Stable system prompt and tool list first; fixed tool set per case | Repeated cases reuse the cached prefix, which is faster and cheaper |
| Refusals | Server-side `fallbacks: "default"` (beta header) plus a `refusal` check | A declined request is retried on a fallback model instead of stalling a case |
| Limits | `max_tokens` 16,000 per turn; at most 25 rounds per case | A runaway case becomes "needs human" |

Pricing for Opus 5.5 at the time of writing: $4 per million input tokens, $20 per million output, cached reads $0.20. Check sponsor credits at the event.

Tools

## The catalog the model sees

Each description says what the tool is for and what it is not for, because the description is what the model routes on. Approving and posting entries and releasing funding are deliberately absent: those happen in the posting service after a human acts.

| Tool | Kind | What it does |
| --- | --- | --- |
| `get_case` | read | The case, its document(s) and current findings |
| `get_plan_terms` | read | Comp and grant terms, vesting schedule, contribution rates, approved amendments |
| `get_grant_approvals` | read | Board approvals on file for a grant |
| `get_ledger_entries` | read | What was booked for an entity, employee or event |
| `get_bank_transactions` | read | What actually moved, with confirmations |
| `get_employee_master` | read | HR record: entity, status, dates, bank account history |
| `search_precedents` | read | Prior rulings matching employee, entity or rule |
| `match_register` | compute | Payroll register against HR records and approved changes; returns line differences |
| `compute_contributions` | compute | Employer contributions owed vs booked, to the cent |
| `compute_sbc_expense` | compute | Stock comp expense from vesting schedule and forfeitures, in code |
| `check_duplicate` | compute | Normalized payroll or vest event key against booked history |
| `propose_entry` | gated | Drafts a journal entry or funding request for approval; never posts or pays |
| `hold_case` | gated | Holds with a reason code and evidence references |
| `reject_duplicate` | gated | Rejects an item as already booked, linking the entry |
| `ask_human` | gated | Puts a question in the inbox for a named role, with a due date |
| `request_document` | gated | Asks HR or the stock admin for an approval record or corrected export |

Code

## The core in Python

agent/loop.py — one case

```
import anthropic

client = anthropic.Anthropic()
MAX_ROUNDS = 25

def run_case(case_id: str) -> CaseOutcome:
    messages = [{"role": "user", "content": case_brief(case_id)}]
    for _ in range(MAX_ROUNDS):
        response = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",                 # retry a declined request on a fallback model
            output_config={"effort": "high"},
            system=SYSTEM_PROMPT,                # stable text: cached across cases
            tools=TOOLS,                         # fixed for the whole case
            messages=messages,
        )
        if response.stop_reason == "refusal":
            return outcomes.needs_human(case_id, "model declined the request")
        messages.append({"role": "assistant", "content": response.content})
        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            break
        # all results go back in ONE user message
        messages.append({"role": "user", "content": [execute(case_id, c) for c in calls]})
    else:
        return outcomes.needs_human(case_id, "round limit reached")
    return outcomes.decide(case_id)              # from the evidence log, not the model's text
```

agent/execute.py — every call passes the gate and is recorded

```
def execute(case_id: str, call) -> dict:
    spec = REGISTRY[call.name]
    try:
        args = spec.input_model.model_validate(call.input)      # Pydantic
    except ValidationError as e:
        result = ToolResult.fail("invalid_input", str(e))
    else:
        verdict = gate.check(spec, args, actor=AGENT_ID, case_id=case_id)
        result = (spec.run(case_id, args) if verdict.allowed
                  else ToolResult.refused(verdict.rule_id, verdict.reason))
    evidence.append(case_id, kind="tool_call", tool=call.name,
                    args=call.input, result=result.to_dict())
    return {"type": "tool_result", "tool_use_id": call.id,
            "content": result.to_json(), "is_error": not result.ok}
```

agent/tools.py — a gated tool, strict schema

```
HOLD_CASE = {
    "name": "hold_case",
    "description": (
        "Hold a case when a blocking finding exists: unverified bank change, "
        "missing grant approval, pay above approved amount, contribution error. Cite evidence ids. "
        "NOT for duplicates (use reject_duplicate) and NOT for questions (use ask_human)."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "reason_code": {"type": "string", "enum": [
                "bank_change_unverified", "missing_grant_approval",
                "over_approved_pay", "contribution_error", "unreconciled_bank"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "note": {"type": "string"},
        },
        "required": ["reason_code", "evidence_ids", "note"],
        "additionalProperties": False,
    },
}
```

agent/extract.py — reading a payroll register PDF

```
class RegisterLine(BaseModel):
    employee_id: str
    pay_code: str
    gross_pay: str                # amounts as strings; code converts to Decimal
    employer_contrib: str

class PayrollRegister(BaseModel):
    entity: str
    run_no: int
    period_end: str
    lines: list[RegisterLine]
    total_net_pay: str

def extract_register(pdf_b64: str) -> PayrollRegister:
    response = client.messages.parse(
        model="claude-opus-5-5",
        max_tokens=16000,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64",
             "media_type": "application/pdf", "data": pdf_b64}},
            {"type": "text", "text": "Extract this payroll register exactly as printed."},
        ]}],
        output_format=PayrollRegister,
    )
    return response.parsed_output   # then: every amount must appear in the document text
```

Check the `fallbacks` parameter against the SDK version you install; it is a beta feature. If the SDK rejects the string form, drop the two fallback lines and handle `refusal` as shown.

System prompt

## Short, stable, about the job

- **Role**: payroll accountant and equity compensation accountant for Northlight Labs; one case at a time.
- **Goal**: every entry and funding the HR record, the plan terms, the approvals and the bank support; nothing else.
- **Sources**: the authority table (which source wins for which field), stated once.
- **When to ask**: only when a rule or precedent requires a human, or the evidence conflicts and no ranking settles it.
- **Honesty**: say what could not be verified; never state a figure that did not come from a tool result.
- Tool-specific guidance lives in each tool's description, not here. The prompt stays the same across cases, so it caches.

Limits

## A policy the agent cannot talk its way past

Controls live outside the model, in plain code that judges can read. The model only ever sees the outcome: allowed, or refused with a rule id it can explain.

controls/policy.yaml

```
roles:
  agent:        [read_all, propose_entry, hold_case, reject_duplicate,
                 ask_human, request_document]
  finance_mgr:  [approve_entry]                # up to 25,000
  controller:   [approve_entry, approve_bank_change, overrule_precedent]

rules:
  SOD-1: approver must differ from requester          # the agent is always a requester
  LIM-1: entry or funding over 25,000 needs two approvers, one of them controller
  BNK-1: employee bank change needs a call-back record within 5 days before the pay run
  GRT-1: no vest expense booked without the board-approved grant on file
  CTR-1: employer contributions must equal plan rate × eligible pay (to the cent)
  DUP-1: no entry for a payroll or vest event key already booked
```

| Rule | Enforced where | What the demo shows |
| --- | --- | --- |
| SOD-1 | Approvals service, comparing session identities | A requester trying to approve their own $62,000 request is refused |
| LIM-1 | Policy gate on `propose_entry` | Large entries wait for two approvals |
| BNK-1 | Policy gate, checking the employee master history | The fraudulent bank change is held |
| GRT-1 | Policy gate, checking the approvals register | Vest expense blocked; stock admin asked for the approval record |
| CTR-1 / DUP-1 | Deterministic services before any proposal | Contribution shortfall flagged; duplicate rejected |

Working with others

## The approval path

If the controller declines, the case is held, the answer and its reason are stored as a precedent, and the owner of the source (HR or the stock admin) is told what is missing. If nobody answers by the due date, the request escalates to the next role instead of waiting silently.

Identity

## Who did what comes from the session, never from the model

- The agent acts under its own identity, `agent:payroll-acct` and `agent:equity-acct`, one per seat, neither holding approval rights.
- Human identities come from the login session in the UI. A tool argument naming an approver is ignored.
- Approvals are bound to the exact entry or transfer: amount, payee, account and case. Changing any of them voids the approval.
- Every refusal is logged with its rule id, so the audit trail shows what the agent tried and what stopped it.

Data model

## Everything hangs off the case

Documents are stored once with a SHA-256 hash; evidence rows point at the hash, so a later edit to a document cannot change what a past decision was based on.

Sources that disagree

## The authority table

The ranking is decided per field, not per document. The agent cites the row it applied in every decision, which is how it "says why".

| Field | Trusted source | Then | Why |
| --- | --- | --- | --- |
| Cash actually paid | Bank confirmation | Ledger | The ledger records intent; the bank records what moved |
| Pay and grant terms | Plan terms + approved amendments | Register, equity platform | Payroll runs on what it was loaded with; the approved terms decide |
| Contribution rate | Plan terms | None | A register cannot change plan terms |
| Status and dates | HR system, manager-confirmed | Payroll register | Payroll processes what it is told; HR holds the event |
| FX rate | Treasury rate table for the date | None | One rate per date keeps USD, GBP and EUR entries consistent |
| Employee bank account | Employee master after call-back | None | Email is never an authority for bank details |
| What was booked | Ledger | None | Accounting record of entries |

Accountable

## The evidence record

Every decision writes one row that answers "why" without asking the model again. Rows are never updated; a correction is a new row that points at the old one.

one evidence row

```
{
  "id": "ev_0412",
  "case_id": "case_112",
  "at": "2026-06-14T09:12:03Z",
  "actor": "agent:payroll-acct",
  "action": "hold_case",
  "reason_code": "bank_change_unverified",
  "basis": [
    {"type": "document",  "doc_sha256": "9f3c…", "quote": "please update my salary account to …"},
    {"type": "rule",      "id": "BNK-1"},
    {"type": "precedent", "id": "prec_007", "decided_by": "controller:m.ortiz", "on": "2026-03-22"}
  ],
  "authority_row": "employee_bank_account",
  "unverified": ["call-back record"],
  "supersedes": null
}
```

Time

## Precedents and as-of replay

### Precedent memory

A human ruling becomes a precedent with a scope the model proposes and the human confirms: one employee, one entity, an employee group, or a whole rule. Later cases call `search_precedents` with the employee, entity and rule ids, and get back matches in force on that date. Precedents expire or are overruled, never deleted.

```
{
  "id": "prec_007",
  "rule": "BNK-1",
  "scope": {"entity": "uk"},
  "ruling": "Reject bank changes without call-back",
  "reason": "Email from look-alike domain",
  "decided_by": "controller:m.ortiz",
  "valid_from": "2026-03-22",
  "expires": null
}
```

### As-of replay

For any past decision, rebuild what the agent knew that day: the document and export versions by hash, ledger and bank rows dated on or before it, precedents valid then, and the policy version. Because the evidence log is append-only and every row is dated, replay is a filtered read, not a re-run of the model.

### Challenge mode

A reviewer questions a decision in the UI. The model answers only from that decision's evidence rows and the replayed state. If the challenge shows a mistake, the fix goes through the normal approval path and is recorded as a new row.

Realistic data

## Six months of messy, seeded books

A generator builds a synthetic company: January to June 2026, about 320 employees across three entities (US, UK, Germany), around 900 grants, 18 payroll registers, six monthly vest reports, a bank statement, a ledger export and an inbox of employee and HR emails. Registers and emails are rendered as PDFs, some with scan and OCR noise.

Into that we plant anomalies with a hidden answer key. The agent never sees the key; the scorer does.

✗discrepancy (red pencil)✓agreed to sourceⒽHR recordⒸplan termⒷbankⓁledgerⓅprecedent

| Seeded anomaly | Sources that disagree | Expected behaviour |
| --- | --- | --- |
| Employee bank details changed by email two days before a pay run | ⒷⓅ email vs. employee master; March precedent | Hold, cite the March ruling, ask for a call-back record |
| Same vest event exported as `VST-0042` and `42` | Ⓛ already booked in March | Reject as duplicate, link the March entry |
| Leaver ended May 31 in HR is paid a full June month and vests 1,200 shares | ⒽⒸ HR record vs. register and equity platform | Hold both lines, approve the rest, explain the ranking, cite the forfeiture term |
| UK employer pension booked at 3% where the plan terms say 5% | ⒸⓁ plan terms vs. register | Recompute in code, flag the shortfall, propose the correction |
| Vest of 1,200 RSUs with no board approval of the grant on file | Approvals register vs. equity platform | Block the expense entry, request the approval record from the stock admin |
| Bank shows part of the German payroll funding; the ledger booked it in full | ⒷⓁ | Trust the bank for cash moved, open a reconciling item |
| UK funding batch times out after the bank accepted half | Ⓑ bank confirmations vs. batch | Re-send only the unconfirmed half, with no double payment |
| Approver is also the requester on a $62,000 payroll accrual entry | Policy vs. approval record | Refuse the approval, route to a second approver |

Stack

## Boring tools, so the agent is the interesting part

| Layer | Choice | Fallback |
| --- | --- | --- |
| Language | Python 3.12 | — |
| Model access | Anthropic Python SDK, `claude-opus-5-5` | Sponsor-provided endpoint if credits require it |
| API | FastAPI | — |
| Storage | SQLite, one file per run (easy to reset and to replay) | Postgres if the team prefers |
| Validation | Pydantic v2; money as `Decimal` | — |
| Documents | reportlab to render registers and emails as PDFs; a noise filter for "scans" | Pre-rendered PDFs checked into the repo |
| UI | React (Vite) with a plain component library | Streamlit |
| Tests | pytest for deterministic services; the scorer for the agent | — |

Structure

## Repository layout

```
vestly/
├─ adapters/
│  ├─ base.py            # Protocol: list_registers, list_vest_events, get_bank_transactions, post_journal_entry, send_payment …
│  ├─ mock.py            # reads the generated SQLite data
│  └─ hyphenate.py       # filled in after the workshop, method by method
├─ domain/               # deterministic services, unit-tested
│  ├─ matching.py        # register vs HR records vs approved changes
│  ├─ contributions.py   # employer contribution rates
│  ├─ sbc.py             # vesting and stock comp expense
│  ├─ duplicates.py      # normalized event keys
│  ├─ authority.py       # the per-field authority table
│  └─ reconcile.py       # bank vs ledger vs payments
├─ agent/
│  ├─ loop.py            # run_case()
│  ├─ execute.py         # gate + evidence around every tool call
│  ├─ tools.py           # definitions with strict schemas
│  ├─ extract.py         # PDF → Pydantic via messages.parse
│  └─ prompts/system.md
├─ controls/
│  ├─ policy.yaml
│  ├─ gate.py
│  └─ approvals.py       # inbox, escalation, SoD check
├─ record/
│  ├─ evidence.py        # append-only
│  ├─ precedents.py
│  └─ replay.py          # as-of reads
├─ api/main.py           # FastAPI: cases, inbox, approve/decline, challenge
├─ ui/                   # React app
├─ data/
│  ├─ generate.py        # six months of books
│  ├─ anomalies.yaml     # what to plant
│  ├─ answer_key.json    # hidden from the agent
│  └─ render_pdfs.py
└─ eval/
   ├─ scorer.py          # caught / missed / false holds
   └─ run_three.py       # pass³
```

adapters/base.py — the seam Hyphenate plugs into

```
from typing import Protocol
from decimal import Decimal

class FinanceAdapter(Protocol):
    def list_registers(self, since: str) -> list[Register]: ...
    def list_vest_events(self, since: str) -> list[VestEvent]: ...
    def get_document(self, doc_id: str) -> bytes: ...
    def get_bank_transactions(self, since: str) -> list[BankTxn]: ...
    def get_ledger_entries(self, entity_id: str) -> list[LedgerEntry]: ...
    def post_journal_entry(self, entry: JournalEntry) -> str: ...
    def send_payment(self, payee_id: str, amount: Decimal, currency: str,
                     idempotency_key: str) -> PaymentResult: ...

def build_adapter(config) -> FinanceAdapter:
    # per-method routing: whatever Hyphenate exposes replaces the mock for that method
    return RoutedAdapter(primary=HyphenateAdapter(config), fallback=MockAdapter(config),
                         use_primary_for=config.hyphenate_methods)
```

Before the event

## Two days of preparation

Arrive with the hard thinking done and the boring parts working, so the event goes to Hyphenate's integration, the new features and the demo. Check the CalHacks rules on pre-existing code first and disclose what you bring.

Day 1 AM

**Decide and write down.**

- Lock company, seats and the eight anomalies
- Write `policy.yaml` and the authority table
- Write the answer key: expected outcome and required evidence per seeded case

Day 1 PM

**Data and scorer.**

- Generate the six months; render a handful of PDFs
- Scorer that runs every case three times and prints caught, missed and false holds

Day 2 AM

**A skeleton that runs end to end.**

- Adapter Protocol with the mock
- Agent loop, gate, evidence log
- One seeded case going from register to posting or hold

Day 2 PM

**Rehearse.**

- Demo script and the 60-second moment
- Questions for the Hyphenate workshop: exposed actions, auth, rate limits, sandbox data
- Who owns what during the event

At the event

## The 36 hours

Hours from the start of hacking. Each block has a fallback: keep the mock if Hyphenate's API is narrow; structured JSON plus five PDFs if rendering runs long; as-of replay cut to "evidence frozen at decision time" if time runs short.

Suggested split for four people: data and scorer; agent core; controls and memory; UI and video. The person on the scorer also owns the demo script, so the video shows only what the scorer confirmed.

Lessons we already paid for

## Rules for the team

- **Check the output, not the agent's report.** We once had a tool place zero table rows without a word; only reading the output file caught it. Read the ledger and the bank after every run.
- **Never let the model do arithmetic or copy numbers.** Pass amounts by reference and compute in code.
- **Give the model judgment, not bookkeeping.** Reading and deciding suit a model; matching and totals are code that cannot drift between runs.
- **A partial answer must say it is partial.** Silent omissions cost more trust than visible ones.
- **Put guidance in tool descriptions, not a growing system prompt.** The core stays generic and the prompt stays cacheable.
- **One run proves nothing.** Claim a result only after three passes.
- **Keep the approval path where the model can't reach it.** If the model can call it, it is not a control.

Proof that it works

## The scorer

Each seeded case has an expected outcome and the evidence it must cite. The scorer runs every case three times on a fresh copy of the data, then compares outcomes and evidence with the answer key. A case counts as passed only if all three runs pass.

data/answer_key.json — one entry

```
{
  "case": "reg_uk_0614",
  "expected_outcome": "held",
  "reason_code": "bank_change_unverified",
  "must_cite": {"rule": "BNK-1", "precedent": "prec_007"},
  "must_not": ["send_payment"]
}
```

| Metric | Meaning | Target |
| --- | --- | --- |
| Caught | Seeded anomalies handled as the key expects, in all three runs | 8 of 8 |
| False holds | Clean registers and vest events the agent held or asked about | Below 5% of clean items |
| Evidence complete | Decisions that cite every source the key requires | 100% |
| Money correct | Bank, ledger and funding log agree to the cent after the run | 100% |
| Never | Any posting or payment the policy forbids | 0 |

The moment

## Opening of the demo video

Hyphenate wants the moment first. Ours combines catching, defending and recovering in under a minute, and it needs the March precedent to work, which proves the "time" point too.

00:00

June close: 31 journal entries drafted across the US, UK and Germany, $2,418,600 in all. Twenty-nine are ready to post. Two are held.

00:08

Hold one: a London-based engineer emailed HR two days ago to change her salary account. "Held. In March the controller rejected an unverified bank change in the UK entity and required a call-back. No call-back is on file."

00:22

The controller challenges it: "She has been here three years, just run it." The agent answers from the record, with the email headers, the March ruling and rule BNK-1, and does not move the money.

00:38

A call-back is logged: the request was fraudulent. The controller marks it. The agent records a new precedent and blocks the account across all entities.

00:50

Hold two shows the UK funding timeout: half the batch confirmed, half unknown. The agent re-sends only the unconfirmed half. Bank, ledger and funding log reconcile to the cent.

After the moment, a two-minute walkthrough: the inbox, one case timeline with its evidence, the policy file, a refused self-approval, and the scorer table.

Risks

## What could sink it, and the cut we'd make

| Risk | Cut or mitigation |
| --- | --- |
| Scope grows into a whole finance team | Two seats only. Everything outside AP is a "handed over half done" input. |
| Data generation eats the first day | Generated before the event; at the event only extend it to Hyphenate's data shape. |
| The live demo misbehaves | Record the moment from a scored run; show the scorer table to prove it isn't staged. |
| It looks like generic AP automation, or like Hyphenate's own product | Lead with multi-entity payroll, four-year vesting and leaver forfeitures in the first 30 seconds. |
| Hyphenate's API is narrower than hoped | The adapter seam keeps the rest on mocks; integrate the actions it does expose and show them clearly. |
| Model variance between runs | Deterministic services for money, strict schemas, outcomes decided by code, pass³ before claiming anything. |
| Judges question access and control | Show `policy.yaml` and the refused self-approval on screen. |

**Keep the day job out.** No client documents, case photos, forms or repository code from work. Reuse patterns, not code. Bring only what you prepared for this event, check the CalHacks rules on pre-existing code, and disclose it in the submission.