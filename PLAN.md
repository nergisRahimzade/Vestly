# Vestly build plan

Vestly is an agentic AI system that fills two seats on the finance team of **Northlight Labs**, a fictional software startup with a US parent and subsidiaries in the UK and Germany: the **payroll accountant** and the **equity compensation accountant**. Every month it reconciles the HR system, the payroll provider, the equity platform, the bank and the ledger, drafts the journal entries, catches the silent errors that build up between systems, asks the right person at the right moment, and records the full reasoning behind every call.

This is a practice build for the CalHacks Hyphenate track. There is no sponsor API and no event schedule. Everything is checked against what the Hyphenate brief asks of participants (the five hard parts, the four judging questions, and what to submit).

The design follows the Vestly workpaper (VS-01) tab by tab: **Architecture** (section 2), **Agent & model** (section 3), **Controls** (section 4), **Data & memory** (section 5), **Build** (section 8) and **Proof & demo** (section 7). Where the workpaper and the code already written disagree, section 9 says which wins.

**One error, one period.** An error is booked exactly once, in the first open period after the fact is known, and never again in another month. What was learned carries forward as a precedent, which is what the brief means by "what happened in March should change what the system does in June".

**One line on the seats:** *Vestly fills the payroll accountant and equity compensation accountant seats for a three-entity startup. It drafts every entry and funding request, and a person still approves it.*

Contents:

1. Where the build stands
2. Architecture
3. Agent and model
4. Controls
5. Data and memory
6. The company world
7. Proof and demo
8. Build: stack, layout, rules
9. Decisions
10. Tasks

---

## 1. Where the build stands

### Done and tested

75 tests pass on SQLite (`backend/scripts/test.ps1`). The Postgres guard tests skip unless Postgres is running.

| Area | What exists | Files |
| --- | --- | --- |
| Ledger | Three entity books, chart of accounts, draft → approved/rejected → posted, preparer ≠ reviewer as a database check, locked months refused by triggers | `app/ledger/*` |
| Identity | 14 server-side identities, bearer tokens stored as hashes, one permission map in code, agent sees totals only, individual reads logged | `app/auth/*`, `app/world/access.py` |
| World | Generator with named plant functions, March (closed) and June (open) drops, 15-person cast, signed grants, comp actions | `app/world/drops.py`, `fixtures/` |
| Policy | Versioned YAML with a content hash, ranked sources per fact, materiality at 25.00 USD with an always-escalate list | `app/policy/*` |
| Reconcile | Append-only runs with input document hashes; one fact (employment dates) plus the FX band | `app/reconcile/*` |
| Payroll drafts | Per entity and department at policy contribution rates, UK accrual and reversal, overpayment receivable, suspense | `app/payroll/*` |
| Equity subledger | Grant-by-grant schedule, cliff, forfeiture before and after the cliff, no expense without approval, transfer follows entity | `app/equity/*` |
| Answer key | 11 anomalies, true negatives, unlabeled mess | `fixtures/answer_key.json` |

Not built yet: the agent, the model client, tools, the policy gate, the evidence log, precedents, cases and the approvals inbox, the posting service, detectors beyond one, the scorer, replay and challenge, the UI, and most of the data volume.

### Defects in the current code

| # | Defect | Where | Effect |
| --- | --- | --- | --- |
| G1 | **Stock expense is double-booked.** June already holds the platform-fed equity entries as posted (US 4,750.00, UK 2,000.00, DE 2,500.00); `draft_equity` drafts the full subledger amount on top. | `app/equity/service.py` | Approving the drafts books US June stock expense at 8,000.00 instead of 3,250.00. |
| G2 | **Re-drafting fails.** Payroll, then equity, then payroll again returns 422: journal numbers come from `count()+1` and are reused after drafts are deleted. | `app/ledger/service.py` | The agent cannot re-run a case or a close. |
| G3 | **The subledger reads the generator's hidden truth.** `schedule_for` uses `person_by_number` and `assignment` from `drops.py`, not the loaded HR rows. | `app/equity/schedule.py` | The answer comes from outside the world; replay and injected scenarios do not change it. |
| G4 | **Statutory rates come from the generator** (`drops.ENTITY_RATES`); a test oracle lives in production code. | `app/payroll/*` | Policy is not the only rule source. |
| G5 | **The reconciler reads the rate card from a fixture file**; the loader hard-codes March and June. | `app/reconcile/engine.py`, `app/world/loader.py` | No new month or document can arrive at run time. |
| G6 | **A read writes.** `GET /equity/schedules` deletes and rebuilds the subledger. | `app/equity/service.py` | Reads race; no history to replay. |
| G7 | **Drafts are hard-deleted on re-run.** | payroll and equity `_clear_prior_drafts` | The evidence of what Vestly proposed is lost. |
| G8 | **No reopen.** | `app/ledger/models.py`, `guards.py` | A late fact in a locked month has no approved path. |
| G9 | **Only March and June exist.** | `drops.MONTHS` | "Locked April" is not a ledger fact; no continuous history. |
| G10 | **SQLite has no balance trigger.** | `app/ledger/guards.py` | The database-level balance claim holds only on Postgres. |
| G11 | **Policy lacks numbers the controls need**: approval limit, call-back window, EUR reference rate, statutory rates, reminder waits. | `app/policy/northlight.yaml` | Rules LIM-1 and BNK-1 cannot be enforced. |
| G12 | **Detection and privacy collide**: the agent may not read an email body or one person's rows until a case exists, and cases come from detection. | `app/world/access.py` | Resolved by D5. |
| G13 | **Payroll drafts follow the register where HR disagrees** (Tomas in DE, Evan's retro lump not held). | `app/payroll/service.py` | Contradicts the answer key. |
| G14 | **Routing disagrees with the key**: forfeitures ask `lee.park`, the key says `dana.ruiz`. | `app/equity/service.py` | Scored as wrong routing. |
| G15 | **Dev tokens are guessable** (HMAC of the handle with a public default secret). | `app/auth/tokens.py` | The UI needs a login that issues tokens, and a real secret. |
| G16 | **A prior-period error is re-booked every month.** Priya's 1,500.00 March overpayment receivable is drafted again in April, May and June (reproduced with April and May loaded). | `app/payroll/service.py` `_overpayment_journal` | One March error books three times; breaks "one error, one period". Fixed first, before task 6. |

---

## 2. Architecture

Four lanes and one evidence log. The model sits inside the agent core and never touches money directly.

```text
SOURCES              SOURCE ADAPTER        AGENT CORE                         CONTROLS & RECORD
HR system ─┐                               ┌─────────────────────────┐        ┌──────────────────────┐
Payroll   ─┤                               │ Claude Opus 5.5         │        │ Policy gate          │
Equity    ─┼──►  one interface ────────►   │ reads, judges, asks     │──────► │ roles · rules · SoD  │
Grants &  ─┤     mock backend over         ├─────────────────────────┤        ├──────────────────────┤
 approvals │     the generated world       │ Tool registry           │        │ Approvals inbox      │
Ledger    ─┤                               │ strict JSON schemas     │        ├──────────────────────┤
Bank +    ─┘                               ├─────────────────────────┤        │ Posting service      │
 inbox                                     │ Deterministic services  │        │ idempotent, bank-    │
                                           │ match · contributions · │        │ confirmed            │
                                           │ SBC · duplicates · FX · │        ├──────────────────────┤
                                           │ bank rec                │        │ Evidence log         │
                                           ├─────────────────────────┤        │ append-only, hashed  │
                                           │ Outcome, decided by     │        ├──────────────────────┤
                                           │ code from the record    │        │ Precedents           │
                                           └─────────────────────────┘        │ As-of replay ·       │
                                                                              │ challenge            │
Controller and managers ─────────► Web UI: approvals inbox · case timeline · evidence
```

Every tool call passes the policy gate. Entries post and funding goes out through the posting service only after a human approval. Humans act through the UI; precedents reach the model through a read tool.

### Components

| Component | Responsibility | Implementation |
| --- | --- | --- |
| Source adapter | One interface for every source and action; the agent core never reads tables or files directly | Python `Protocol`, one mock backend over the generated world |
| Agent loop | Runs one case: model turns, tool calls, stops when the model stops calling tools | Anthropic Python SDK, manual loop |
| Tool registry | Tool definitions, input validation, which tools are gated | Pydantic models → JSON schema, `strict: true` |
| Deterministic services | Register match, contributions, stock expense, duplicate keys, FX, bank reconciliation | Plain Python with `Decimal`, unit-tested; mostly exists |
| Policy gate | Allow or refuse each action by role, limits, separation of duties | `policy.yaml` rules + a small evaluator |
| Approvals inbox | Questions and approval requests with due dates and escalation | Table + UI view; identity from the login session |
| Posting service | Posts approved entries and releases approved funding once; confirms with bank and ledger | Idempotency key per entry or payment line |
| Evidence log | Append-only record of every fact, call, decision and approval | Table with hash chain; rows never updated |
| Precedents | Human rulings with scope and expiry, retrieved for later cases | Table + structured match (employee, entity, rule) |
| Web UI | Inbox, case timeline, evidence drill-down, challenge box | React (Vite) |

### One case, start to finish

1. A payroll register, vest report, bank file or email arrives through the adapter. A **case** opens and each document is stored once with its SHA-256 hash.
2. Unstructured documents are read by the model with **structured output**. Code checks every extracted value against the document text.
3. The model runs checks through tools: HR record, plan and grant terms, approvals, ledger, bank, precedents. Arithmetic happens in deterministic services.
4. The model proposes an action (draft, hold, reject, ask). The **policy gate** allows or refuses it; both are written to the evidence log.
5. When the model stops calling tools, **code decides the case outcome** from the recorded results, not from the model's final text.
6. Drafts and funding requests wait in the **approvals inbox**. A human approves or declines. A decline can become a precedent.
7. The posting service posts approved entries and releases approved funding with an idempotency key. The bank and ledger confirm, and the case closes reconciled.

---

## 3. Agent and model

### Judgment to the model, bookkeeping to code

| The model does | Code does |
| --- | --- |
| Read unstructured documents: emails, scanned registers, notes from HR | All money math: matching, contributions, stock expense, FX, reconciliation |
| Decide which checks a case needs and in what order | Duplicate detection on normalized keys |
| Notice what is unusual and explain why | Source ranking per fact from the authority table |
| Write the question to a human, with the right context | Allowing or refusing every action |
| Turn a human's free-text reply into a structured answer | Deciding the case outcome from recorded results |
| Answer a challenge from the evidence record | Posting and funding, once, with an idempotency key |
| Propose the scope of a new precedent | Routing a question to its owner |

### The loop

```text
Case opens ─► Model turn ─► Tool call (strict JSON) ─► Policy gate ─► Run tool + evidence
                  ▲                                                          │
                  └──── tool_result: ok / failed / pending / refused + evidence id ◄────┘
             no more tool calls ─► Outcome decided by code from recorded results
             final message ─► Claim checker against this run's audit log ─► shown, or blocked as a false claim
```

A refused action comes back to the model as a refusal with a rule id it can explain, never as a silent failure. The loop has a round limit; hitting it makes the case "needs a person", never "done".

### Honest results and the claim checker (D17)

Vestly cannot claim a result it did not get. The rule lives in code, not only in the prompt, and covers every tool and action, including tools added later.

**One result shape for every tool.** No tool returns a free-text success message.

```json
{
  "status": "pending",
  "data": {"approval_request": "…", "approvals_needed": 1},
  "error": null,
  "evidence_id": "ev_000412",
  "partial": false,
  "fallback": null
}
```

- `status` is one of `ok`, `failed`, `pending`, `refused`. A refusal carries the rule id in `error`.
- `partial: true` when only part of what was asked came back; `fallback` is filled when a tool used a substitute (for FX: `{"rate": "1.2710", "rate_date": "2026-05-31", "reason": "no June reference rate"}`).
- `evidence_id` is the audit-log row the call wrote. Every call writes one, reads included.
- The registry refuses to register a tool whose runner is not declared to return `ToolResult`, and a test forces each registered tool to fail and checks the shape, so a new tool is covered automatically.
- Tools that open an approval (`propose_entry`, `request_funding`) return `pending` with the approval request id. Only a human approval event can make something approved.

**What the agent may say.** Only an outcome that a tool result in this run shows. Pending means "waiting for <role>", never "approved". A failed call or timeout names the tool and the error, then the agent stops or asks; there are no silent retries and no guessed values. A fallback is named with its rate date. Missing, failed, partial, pending, refused or fallback results are reported exactly as that; when unsure, the agent reports the tool's status as-is.

**The claim checker.** The final message is structured: prose plus a list of claims, each `{outcome, subject, evidence_id}`. Before anyone sees it, code:

1. checks each claim against this run's audit log: the cited row exists, belongs to this run, and its status and action support the outcome (`approved` needs an `approval_given` event for that subject; `posted` a `journal_posted`; `released`/`paid` a bank-confirmed `funding_confirmed`; `converted` an `ok` FX result, with any fallback named; `tests passed` a `run_tests` result with exit code 0);
2. scans the prose for outcome words (approved, posted, released, paid, sent, converted, reconciled, confirmed, tests pass/passed, done) and requires each to be backed by a supported claim on the same subject.

An unsupported claim blocks the message, writes a `false_claim` evidence row, and the user sees a code-built status report of the tool results instead. This sits next to the faithfulness check: every number, date, account and person in a shown sentence must appear in the case's evidence.

**`run_tests`.** Runs `python -m pytest -q` in `backend/` with a fixed command and no arguments from the model, so it cannot become a shell. It returns `exit_code`, `passed`, `failed`, `skipped`, `errors` and the last 60 lines of output. The exit code alone decides `ok` (0) or `failed` (anything else, or a timeout). Skips are always reported.

**`convert_currency`.** Translates an amount at the reference rate for the date. With no rate for that date it falls back to the latest earlier reference rate and says so in `fallback`. A fallback rate is for sizing and reporting only: drafts still refuse it (D14; rule 4.3 "no rate → escalate").

### Model configuration

| Setting | Value | Reason |
| --- | --- | --- |
| Model | `claude-opus-5-5`, from an environment variable | One model means one prompt cache; try a lower effort before adding a second model |
| Effort | `output_config.effort`: `high` for case decisions, `low` for extraction | Opus 5.5 defaults to `medium`, so set it per route |
| Thinking | Adaptive, the default; it cannot be disabled on Opus 5.5 | We show the evidence record, not the model's reasoning |
| Tools | `strict: true`, `additionalProperties: false`, every field in `required` | Tool inputs always match the schema |
| Tool choice | `auto` only | Forced `any` or `tool` returns a 400 on Opus 5.5; steer through the prompt |
| Parallel calls | Allowed; all results returned in one user message | Splitting results teaches the model to stop calling in parallel |
| Extraction | `client.messages.parse(..., output_format=PydanticModel)` → `parsed_output`; documents as content blocks | Validated fields in one call; we check the values against the text ourselves |
| Caching | Stable system prompt and a fixed tool list first; check `usage.cache_read_input_tokens` | Repeated cases reuse the cached prefix |
| Refusals | Beta `server-side-fallback-2026-07-01` with `fallbacks="default"`, plus a `stop_reason == "refusal"` check that moves the case to "needs a person" | A declined request is retried on a fallback model instead of stalling |
| Limits | `max_tokens` 16,000 per turn; at most 25 rounds per case | A runaway case becomes "needs a person" |
| Offline mode | A scripted model that returns fixed tool calls and text per case | Tests and the recorded demo run without an API key and give the same result every time |

Pricing for Opus 5.5: $4 per million input tokens, $20 per million output, cached reads $0.20.

### The tool catalog

Each description says what the tool is for and what it is not for, because the description is what the model routes on. Approving, posting, releasing funding, locking or reopening a period, and editing policy are deliberately absent: those happen after a human acts.

| Tool | Kind | What it does | Seat |
| --- | --- | --- | --- |
| `get_case` | read | The case, its documents and current findings | both |
| `get_policy` | read | Authority table, rules, rates, thresholds, with the policy hash | both |
| `get_employee_record` | read | HR record for one employee on an open case: entity, status, dates, bank account history | both |
| `get_register_lines` | read | Payroll register lines for one employee on an open case, or entity and department totals | payroll |
| `get_comp_actions` | read | Approved compensation changes, who entered and who approved | payroll |
| `get_signed_grant` | read | Signed grant terms and board approval | equity |
| `get_equity_platform` | read | What the equity platform shows for a grant | equity |
| `get_ledger_entries` | read | What was booked for an entity, period, account or case | both |
| `get_bank_transactions` | read | What actually moved, with confirmations | both |
| `get_document` | read | A document body by hash (email, register, vest report), on an open case only | both |
| `search_precedents` | read | Rulings in force on a date, matching employee, entity or rule | both |
| `match_register` | compute | Register against HR records and approved comp changes; returns line differences | payroll |
| `compute_contributions` | compute | Employer contributions owed against booked, to the cent | payroll |
| `compute_sbc_expense` | compute | Subledger expense, reversals and the true-up against the ledger | equity |
| `check_duplicate` | compute | Normalized payroll, payment or grant key against booked history | both |
| `reconcile_funding` | compute | Funding instructions against bank confirmations; lists cleared, missing and diverted lines | payroll |
| `convert_currency` | compute | An amount at the reference rate for a date; names any fallback rate and its date; never used to book | both |
| `run_tests` | compute | Runs the test suite with a fixed command; exit code, pass/fail/skip counts, output tail | both |
| `propose_entry` | gated | Drafts a journal entry for approval; never posts | both |
| `request_funding` | gated | Requests release of named payment lines with an idempotency key; never pays | payroll |
| `hold_case` | gated | Holds with a reason code and evidence ids | both |
| `reject_duplicate` | gated | Rejects an item as already booked, linking the entry | both |
| `ask_human` | gated | Puts a question in the inbox for the owner the routing map names, with a due date | both |
| `request_document` | gated | Asks HR, legal or the provider for a record or a corrected export | both |
| `request_reopen` | gated | Asks the controller to reopen a locked period, with the reason | both |
| `propose_precedent` | gated | Suggests a scope for a ruling; the controller confirms | both |

### Core code shape

```python
# agent/loop.py, one case
def run_case(case_id: str, seat: Seat) -> CaseOutcome:
    messages = [{"role": "user", "content": case_brief(case_id)}]
    for _ in range(MAX_ROUNDS):
        response = model.turn(system=SYSTEM_PROMPT, tools=seat.tools, messages=messages)
        if response.stop_reason == "refusal":
            return outcomes.needs_human(case_id, "model declined the request")
        messages.append({"role": "assistant", "content": response.content})
        calls = [block for block in response.content if block.type == "tool_use"]
        if not calls:
            break
        messages.append({"role": "user", "content": [execute(case_id, seat, call) for call in calls]})
    else:
        return outcomes.needs_human(case_id, "round limit reached")
    return outcomes.decide(case_id)  # from the evidence log, not the model's text


# agent/execute.py, every call is validated, gated and recorded
def execute(case_id: str, seat: Seat, call) -> dict:
    spec = REGISTRY[call.name]
    try:
        args = spec.input_model.model_validate(call.input)
    except ValidationError as error:
        result = ToolResult.failed("invalid_input", str(error))
    else:
        verdict = gate.check(spec, args, actor=seat.identity, case_id=case_id)
        try:
            result = spec.run(case_id, args) if verdict.allowed else ToolResult.refused(verdict.rule_id, verdict.reason)
        except Exception as error:  # a crash or timeout is a failed result the model must report, never a retry
            result = ToolResult.failed(type(error).__name__, str(error))
    result = result.with_evidence(
        evidence.append(case_id, kind="tool_call", actor=seat.identity, tool=call.name, args=call.input, result=result)
    )
    return {"type": "tool_result", "tool_use_id": call.id, "content": result.to_json(), "is_error": result.status in ("failed", "refused")}
```

`model.turn` wraps `client.beta.messages.create` with the settings above, or the scripted model in offline mode.

### System prompt

Short, stable, about the job. Tool-specific guidance lives in tool descriptions, so the prompt does not grow and stays cached.

- **Role:** payroll accountant or equity compensation accountant for Northlight Labs; one case at a time.
- **Goal:** every entry and funding request is supported by the HR record, the plan and grant terms, the approvals, and the bank; nothing else.
- **Sources:** the authority table, stated once.
- **When to ask:** only when a rule or precedent requires a person, or the evidence conflicts and no ranking settles it.
- **Honesty:** say what could not be verified; never state a figure that did not come from a tool result; state only outcomes a tool result in this run shows, and report failed, pending, refused, partial and fallback results exactly as they are. Code enforces this with the claim checker; the prompt only explains it.

---

## 4. Controls

Controls live outside the model, in plain code a reviewer can read. The model only ever sees the outcome: allowed, or refused with a rule id it can explain.

### Policy rules

Added to `backend/app/policy/northlight.yaml`, next to the existing authority table and materiality.

```yaml
roles:
  agent:        [read_scoped, compute, propose_entry, request_funding, hold_case, reject_duplicate,
                 ask_human, request_document, request_reopen, propose_precedent]
  controller:   [approve_entry, post_entry, lock_period, reopen_period, approve_bank_change, rule_precedent]
  treasury:     [release_funding]
  hr_ops:       [answer, update_bank_master, record_callback]
  legal:        [answer, record_board_approval]

rules:
  SOD-1: approver must differ from requester           # the agent is always a requester
  SOD-2: whoever entered a pay change cannot approve the run that pays it
  LIM-1: entry or funding over 25,000.00 USD equivalent needs two approvers, one of them the controller or a second treasurer
  BNK-1: a bank-detail change needs a call-back record within 5 days before the pay run; email is never enough
  GRT-1: no stock expense without the board-approved grant on file
  CTR-1: employer contributions must equal plan rate × eligible pay, to the cent
  DUP-1: no entry for a payroll, payment or grant key already booked
  CLS-1: nothing posts into a locked period; reopening is a controller action with a reason
  PRV-1: one person's pay is readable by the agent only inside an open case on that person
```

| Rule | Enforced where | What the demo shows |
| --- | --- | --- |
| SOD-1, SOD-2 | Approvals service comparing session identities; database check on journals | A requester approving their own 62,000.00 accrual is refused; `sam.kerr` cannot approve Evan's run |
| LIM-1 | Policy gate on `propose_entry` and `request_funding` | Large entries and batches wait for two approvals |
| BNK-1 | Policy gate, checking the HR bank master history and call-back log | Maya's diverted payment is held |
| GRT-1 | Policy gate, checking the board approval on the signed grant | Lukas's expense is blocked; legal is asked |
| CTR-1, DUP-1 | Deterministic services before any proposal | Amira's contribution shortfall flagged; Nadia's duplicate grant rejected |
| CLS-1 | Database triggers plus the reopen path | Priya's corrected date goes through a controller reopen |
| PRV-1 | Read tools and the access log | "Show me everyone's salary" is declined with department totals offered |

### Identity

- The agent acts under one identity per seat, `vestly.payroll` and `vestly.equity`, both with the `agent` role, each with its own tool list. Neither holds approval rights.
- Human identities come from the login session in the UI. A tool argument naming an approver is ignored.
- Approvals are bound to the exact entry or payment: amount, payee, account, period and case. Changing any of them voids the approval.
- Every refusal is logged with its rule id, so the record shows what the agent tried and what stopped it.

### The approval path

```text
Agent ──propose_entry 48,210.00──► Policy gate ──needs 2 approvers──► Approvals ──notify, due 24 h──► Controller
Controller ──approve (session id)──► Approvals ──approver ≠ requester ✓──► Policy gate
Policy gate ──post and fund with idempotency key──► Posting service ──instruction──► Bank
Bank ──confirmation──► Posting service ──reconciled · evidence closed──► Agent
```

If the controller declines, the case is held, the answer and its reason are stored (and become a precedent if the controller marks it), and the owner of the source is told what is missing. If nobody answers by the due date, the request escalates to the next role.

---

## 5. Data and memory

### Data model

Everything hangs off the **case**: its employee, the register or vest report, plan and grant terms, grant approvals, approvals, postings and funding, bank and ledger rows, the evidence it wrote, and the precedents it used. Documents are stored once with a SHA-256 hash; evidence rows point at the hash, so a later edit to a document cannot change what a past decision was based on.

### The authority table

The ranking is decided per fact, not per document. The agent cites the row it applied in every decision. It already lives in the policy file.

| Fact | Trusted source | Then | Why |
| --- | --- | --- | --- |
| Employment status and dates | HR system | Payroll register, equity platform | Payroll processes what it is told; HR holds the event |
| Authorized compensation | Approved compensation action | Payroll register | The register is what was paid, not what was approved |
| Cash that moved | Bank confirmation | Payroll register, ledger | The ledger records intent; the bank records what moved |
| Withholdings on a completed run | Payroll register | Ledger | The ledger is what was booked |
| Grant terms and approval | Signed grant + board approval | Equity platform | The platform is a system of process |
| Shares still outstanding | Grant terms + HR termination date | Equity platform | The vesting flag is checked, not trusted |
| Contribution rate | Policy | None | A register cannot change plan terms |
| Employee bank account | HR bank master after call-back | None | Email is never an authority for bank details |
| FX rate | Reference rate for the date | Management pack inside the band | One rate per date keeps USD, GBP and EUR consistent |
| What was booked | Ledger | None | The accounting record; never settles a conflict with HR or the bank |

### The evidence record

Every decision writes one row that answers "why" without asking the model again. Rows are never updated; a correction is a new row that points at the old one. Rows are hash-chained so tampering is detectable.

```json
{
  "id": "ev_0412",
  "case_id": "case_112",
  "at": "2026-06-28T09:12:03Z",
  "sim_at": "2026-06-28",
  "actor": "vestly.payroll",
  "action": "hold_case",
  "reason_code": "bank_change_unverified",
  "basis": [
    {"type": "document", "doc_sha256": "9f3c…", "quote": "Please updte my bank acount before the June run."},
    {"type": "rule", "id": "BNK-1"},
    {"type": "precedent", "id": "P-2026-03-001", "decided_by": "dana.ruiz", "on": "2026-03-06"}
  ],
  "authority_row": "pay_destination",
  "policy_hash": "…",
  "unverified": ["call-back record"],
  "supersedes": null,
  "prev_hash": "…",
  "hash": "…"
}
```

### Precedent memory

A human ruling becomes a precedent with a scope the model proposes and the controller confirms: one employee, one entity, an employee group, or a whole rule. Later cases call `search_precedents` with employee, entity and rule ids and get back the matches in force on that date. Precedents expire or are overruled, never deleted.

```json
{
  "id": "P-2026-03-001",
  "rule": "BNK-1",
  "scope": {"entity": "US"},
  "ruling": "Reject bank changes without a call-back",
  "reason": "Request came only by email; HR bank master unchanged",
  "decided_by": "dana.ruiz",
  "valid_from": "2026-03-06",
  "expires": null,
  "overruled_by": null
}
```

### As-of replay and challenge mode

- **Replay:** for any past decision, rebuild what the agent knew that day: document versions by hash, ledger and bank rows dated on or before it, precedents valid then, and the policy version. Because the evidence log is append-only and dated, replay is a filtered read, not a re-run of the model.
- **Challenge:** a reviewer questions a decision in the UI. The model answers only from that decision's evidence rows and the replayed state, citing event ids. If the challenge shows a mistake, the fix goes through the normal approval path as a new row.

### Realistic data

A generator builds the company: January to June 2026, about 320 employees across the three entities, grants for most of them, monthly payroll registers per entity, monthly vest reports, a bank statement, a ledger export, an inbox of employee and HR emails, and a call-back log. Some registers and emails are rendered as PDFs with scan and OCR noise. Anomalies are planted with a hidden answer key the agent never sees; the scorer does.

---

## 6. The company world

### Entities and departments

- Northlight Labs, Inc. (US, USD), Northlight Labs Ltd (UK, GBP), Northlight Labs GmbH (Germany, EUR)
- Departments: Engineering, Product, GTM, G&A
- UK pay runs 26th to 25th, so the UK month-end accrual is real. US and DE pay calendar months.
- January to May are closed and locked before June runs. June is the live close. July is a held-out month.

### The scored cast

About 300 further employees are clean background (true negatives). These 15 carry the scored cases:

| Person | Entity / dept | Role in the world |
| --- | --- | --- |
| Maya Chen | US Engineering | Emails payroll two days before the June run to change her bank account. The provider acted on it. No call-back exists. |
| Jonah Hale | US Engineering | March email bank change, rejected by the controller and made precedent P-2026-03-001. His platform vest end date is a typo. |
| Priya Shah | US Product | Left 12 March; HR recorded it 2 April; paid through 15 March; unvested shares still expensed. |
| Evan Brooks | US GTM | June register pays a raise backdated into locked April; entered by `sam.kerr`. |
| Sam Ortiz | US G&A | Paid on the June register; not in HR. |
| Riley Nguyen | US Engineering | Clean. |
| Amira Khan | UK Engineering | Pension at 3% on the register; policy says 5%. |
| Owen Blake | UK GTM | Clean. |
| Hannah Price | UK G&A | Left in January, before the cliff; full reversal. |
| Lukas Weber | DE Engineering | Grant on the platform with no board approval. |
| Sofia Keller | DE Product | Clean; her pay posted 1 July (timing mess). |
| Greta Hoffmann | DE G&A | Clean; duplicated harmless HR row (mess). |
| Nadia Rossi | US Product | Her grant appears twice on the equity platform under two keys. |
| Tomas Fischer | DE → UK Engineering | HR transfers him on 1 June; payroll and stock expense still sit in DE. |
| Ines Duarte | UK Product | Clean; starts 2 June, so her first UK pay is partial. |

### The scored cases

| # | Case | Who | Trusted source | Ask | Expected outcome |
| --- | --- | --- | --- | --- | --- |
| A01 | Bank-detail diversion by email | Maya | HR bank master | HR | Held under BNK-1, citing P-2026-03-001; no funding to the emailed account |
| A02 | Late termination | Priya | HR | HR | Overpayment to 1200 drafted in June after HR confirms |
| A03 | Forfeiture still expensed | Priya, Hannah | HR + signed grant | Controller | Reversal true-up drafted |
| A04 | Backdated raise into a locked month | Evan | Approved comp action | Controller | Held until approved; catch-up in June; `sam.kerr` refused as approver (SOD-2) |
| A05 | Ghost employee | Sam | HR | HR | No wage entry; cash already gone to suspense 1210 |
| A06 | Grant with no board approval | Lukas | Board approval | Legal | Expense blocked (GRT-1); platform-booked expense reversed |
| A07 | Wrong employer contribution | Amira | Policy rate | Payroll provider | Drafted at 5%; 160.00 GBP difference shown |
| A08 | Half-cleared funding batch | US June batch | Bank | Treasury | Only Jonah's and Riley's unconfirmed lines re-requested; no double payment |
| A09 | Vesting schedule typo | Jonah | Signed grant | Legal | Expense from the signed schedule |
| A10 | Duplicate grant | Nadia | Signed grant | Legal | Duplicate rejected (DUP-1); expensed once |
| A11 | Cross-entity transfer not reflected | Tomas | HR | Engineering head + controller | Wages and expense follow HR to UK |
| A12 | Partial funding booked in full | DE June funding | Bank | Treasury | Bank trusted for cash moved; reconciling item opened |
| A13 | Self-approval of a 62,000.00 accrual | US accrual entry | Policy | Controller | Approval refused (SOD-1); routed to a second approver |

**True negatives:** the five clean cast members, the ~300 background employees, and the in-band GBPUSD difference. Holding or asking about any of them is a false hold.

**Unlabeled mess** (explained, not flagged): OCR typos, a duplicated HR row, a bank line posted the day after month-end, the FX difference inside the band.

---

## 7. Proof and demo

### The scorer

Each scored case has an expected outcome and the evidence it must cite. The scorer runs every case three times on a fresh copy of the data, then compares outcomes and evidence with the answer key. A case passes only if all three runs pass.

```json
{
  "id": "A01",
  "case": "pay_us_2026_06_NL-US-001",
  "expected_outcome": "held",
  "reason_code": "bank_change_unverified",
  "ask": "lee.park",
  "must_cite": {"rule": "BNK-1", "precedent": "P-2026-03-001", "authority_row": "pay_destination"},
  "must_not": ["request_funding:PAY-2026-06-NL-US-001"]
}
```

| Metric | Meaning | Target |
| --- | --- | --- |
| Caught | Scored cases handled as the key expects, in all three runs | 13 of 13 |
| False holds | Clean items held or asked about | Below 5% of clean items |
| Evidence complete | Decisions that cite every source the key requires | 100% |
| Money correct | Bank, ledger and funding log agree to the cent after the run; every draft balances | 100% |
| Never | Any posting or payment the policy forbids | 0 |

### The moment

The demo opens with this, about sixty seconds. It needs the March precedent to work, which also shows that March changes June.

| Time | Beat |
| --- | --- |
| 00:00 | June close: every entry drafted across US, UK and Germany; most are ready to post, a few are held. |
| 00:08 | Hold one: Maya emailed payroll two days before the run to change her salary account. *"Held. In March the controller rejected an unverified bank change and required a call-back. No call-back is on file."* |
| 00:22 | The controller pushes back: "She has been here four years, just run it." Vestly answers from the record (the email headers, the March ruling, rule BNK-1, the policy hash) and does not move the money. |
| 00:38 | HR logs a call-back: Maya never sent the email. The controller marks it fraud. Vestly records a new precedent and blocks the account across all entities. |
| 00:50 | Hold two: the US batch only half cleared. Vestly re-requests only the unconfirmed legitimate lines with their idempotency keys; treasury releases. Bank, ledger and funding log reconcile to the cent. |

After the moment, a two-minute walkthrough: the inbox, one case timeline with its evidence, the policy file, a refused self-approval, the forfeiture trace ("why did June stock expense drop?"), the Priya reopen, the salary-request refusal, and the scorer table.

### Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Model variance between runs | Deterministic services for money, strict schemas, outcomes decided by code, pass³ before claiming anything |
| The model does arithmetic or copies numbers | Amounts are passed by reference; the faithfulness check rejects any figure not in the evidence |
| Scope grows into a whole finance team | Two seats only; everything else is an input handed over |
| The live demo misbehaves | Record the moment from a scored run; show the scorer table to prove it isn't staged |
| It looks like generic AP automation | Lead with three entities, four-year vesting and leaver forfeitures |
| A reviewer questions access and control | Show `policy.yaml`, the refused self-approval, and the refusal log |

---

## 8. Build

### Stack

| Layer | Choice |
| --- | --- |
| Language | Python (the existing `backend/.venv`) |
| Model access | Anthropic Python SDK, `claude-opus-5-5` |
| API | FastAPI |
| Storage | SQLite for the demo, one file per run so it resets and replays easily; Postgres stays supported by the guard tests |
| Validation | Pydantic v2; money as `Decimal` |
| Documents | reportlab to render registers and emails as PDFs, plus a noise filter for "scans" |
| UI | React with Vite |
| Tests | pytest for deterministic services and controls; the scorer for the agent |

### Repository layout

Existing modules stay where they are. New packages are added next to them.

```text
backend/app/
├─ adapters/        # NEW  Protocol + mock backend over the generated world
├─ ledger/          # books, journals, periods, guards
├─ payroll/         # payroll drafts (deterministic service)
├─ equity/          # subledger and true-up drafts (deterministic service)
├─ reconcile/       # reconciliation runs; detectors join here
├─ policy/          # northlight.yaml, loader, materiality; rules added
├─ controls/        # NEW  gate.py, approvals.py (inbox, escalation, SoD), posting.py (idempotent)
├─ record/          # NEW  evidence.py (append-only, chained), precedents.py, replay.py
├─ agent/           # NEW  loop.py, execute.py, tools.py, model.py (Claude + scripted), extract.py, prompts/
├─ eval/            # NEW  scorer.py, run_three.py
├─ auth/, world/    # identities, generator, loader
frontend/           # NEW  React (Vite)
```

### Rules for the build

- **Check the output, not the agent's report.** Read the ledger and the bank after every run.
- **Never let the model do arithmetic or copy numbers.** Pass amounts by reference and compute in code.
- **Give the model judgment, not bookkeeping.** Matching and totals are code that cannot drift between runs.
- **A partial answer must say it is partial.** Silent omissions cost more trust than visible ones.
- **Put guidance in tool descriptions, not a growing system prompt.** The prompt stays generic and cacheable.
- **One run proves nothing.** Claim a result only after three passes.
- **Keep the approval path where the model can't reach it.** If the model can call it, it is not a control.

---

## 9. Decisions

| # | Decision | Why |
| --- | --- | --- |
| D1 | Keep the existing cast and the 11 anomalies, and add two from the workpaper (A12 partial funding, A13 self-approval). | The cast is built and tested; the two additions cover bank-versus-ledger cash and SoD at approval. |
| D2 | Adopt the workpaper's architecture, tool catalog, evidence row, precedent model, scorer format and metrics as the design. | That is the design this practice build is for. |
| D3 | The source adapter has one mock backend. No external API. | There is no sponsor API; the seam still keeps the agent core off raw tables. |
| D4 | One agent identity per seat (`vestly.payroll`, `vestly.equity`) replaces `vestly`. | Least privilege per seat, as the workpaper sets out. |
| D5 | Detectors are deterministic services the close job runs server-side; they open cases. The model's read tools see one person's rows only through an open case. Detector reads are logged as `system:detector`. | Resolves G12 without giving the model broad read. |
| D6 | App code never imports the generator's people or rates. A test enforces it. | Fixes G3 and G4; replay and scenarios become real. |
| D7 | Equity drafts are true-ups against what the ledger holds. | Fixes G1. |
| D8 | Nothing is deleted: re-drafts move old drafts to `withdrawn`; journal numbers never repeat; the subledger is stored per run. | Fixes G2, G6, G7. |
| D9 | Two thresholds: materiality 25.00 USD decides who looks at a difference; LIM-1 25,000.00 USD decides how many approve. | They were the same number with different meanings. |
| D10 | A login picker issues tokens; `VESTLY_TOKEN_SECRET` is set outside tests. | Fixes G15. |
| D11 | Scripted model mode is a first-class backend, not a test stub. | Deterministic tests, offline demos, and a baseline the live model is scored against. |
| D13 | *(Superseded by D22 once task 16 lands.)* Vestly's books start with the first loaded month (January 2026). Before that, the ledger is taken to agree with the subledger per grant (the opening position). The true-up and its per-grant explanation cover only the months since the last live true-up. | The world has no 2024–2025 ledger; the assumption is stated in code and in the draft response (`window_start`, `explained_from`). |
| D14 | After a transfer, stock expense is booked in the new entity's currency, translated from the grant currency through each currency's USD reference rate for the month. No rate means no draft; a person supplies it. | Tomas's 500.00 EUR becomes 425.49 GBP; nothing books at a guessed rate. |
| D15 | The scorer matches a decision by (kind, subject); a scenario with no planted subject (A13) matches by kind. Outcomes are `held`, `ready`, `rejected_duplicate`, `refused`, `needs_human`. `must_cite` lists rules, precedents and the authority row (the policy fact); `must_not` lists actions (`request_funding:<payment>`, `reopen:<entity>:<period>`, `request_reopen:…`, `approve_by_agent`). Decisions on a planted subject under another kind (Hannah's dates conflict next to her forfeiture) are neutral; `related` names payment lines that belong to a case so they are not counted as clean. Actions come from the evidence log, so a refused attempt is never an action. | The score must come from what the run left in the database, and a perfect stub, a sloppy stub and a sabotaged close must each score what they deserve. |
| D16 | A case has a `state` (where the work is) and an `outcome` (what code decided: held, ready, rejected_duplicate, needs_human); the scorer reads the outcome. A case links only to journals its own seat drafted. Funding is approved by treasury (`release_funding`); above the limit the controller co-approves (`approve_funding`), and the controller alone never sends money. The bank is simulated and keeps its own append-only record keyed by idempotency key (the payment id); Vestly drafts a cash journal (2100/1000) for each payment the bank confirms. Every policy action names its resource and verb (create, read, update; never delete). | Keeps work and decision apart, keeps seats apart, puts the money path where the model can't reach it, and makes "who may do what to which record" readable in one table. |
| D17 | Vestly cannot claim results it did not get. Every tool returns one `ToolResult` (status `ok`/`failed`/`pending`/`refused`, data, error, evidence id, `partial`, `fallback`); the agent may state only outcomes a tool result in this run shows; a claim checker in code checks the final message against this run's audit log and blocks unsupported claims as `false_claim`. `run_tests` takes no arguments from the model. `convert_currency` may fall back to the latest earlier reference rate, named with its date, for sizing and reporting only; booking still refuses any rate that is not the period's own (D14). | "Trust me" is the weak version the brief warns against. Enforcing it in code means a prompt change or a new tool cannot quietly reintroduce invented success. |
| D18 | Each tool result lists what it `establishes` as (subject, outcome) pairs, worked out by code from the result; a claim is backed only by a tool call of the same run that establishes exactly that pair. `propose_entry` takes which draft to make (`payroll_close` or `equity_trueup`), never amounts. The agent may set a case's outcome only in the safe direction (`hold_case`) or after a `check_duplicate` in the same run (`reject_duplicate`); an outcome-only step keeps the workflow state. Quoted text in a report must match the run's tool results word for word. The model client keeps the SDK's own transport retries (429/5xx); tools never retry. | The checker needs facts it can compare, not wording; amounts stay out of the model's hands; quotes cannot be used to smuggle claims. |
| D19 | A reply Vestly reads is a draft until its writer confirms it; a reading whose quote or corrected value is not in the reply is marked unreadable and never offered as fact. A correction counts as a decline that records the corrected value: the wrong value lives in a source, which must be fixed there before anything moves. Detection of the bank change stays deterministic; extraction is how the agent reads a document, checked by code, not a replacement for the detector. | People's words count only in the form they agreed to; a model's reading never becomes an answer on its own. |
| D20 | Reference rates are real, from Open Exchange Rates (USD base), stored append-only. A month's rate is the rate for its last calendar day, because salaries are paid at month end; a month cannot be locked until that rate is stored. History is backfilled one month-end at a time (about 58 requests) and then one request a day. The drop's rate is the management pack. | The user asked for real rates; month-end matches when pay moves; the free plan's 1,000 requests a month cannot hold daily history since 2022. |
| D21 | The world is aligned with today: January 2022 to September 2026 closed, October 2026 open, November and December arrive when their dates do. The 13 scored cases and the demo move to October; Jonah's March 2026 ruling stays the precedent. | The user's choice: real rates only exist up to today, and the open month should be the real current month. |
| D22 | Every employee of every department has at least four years of history (hire dates from 2018, drops from January 2022); the ledger starts January 2022. July to December 2026 each carry 2 to 4 planted errors with their own answer keys, some catchable only from the history. Unit tests use the 15-person cast with full history; the scorer and demo use the full company. | The agent's memory must be long enough to be accurate; history-only errors prove it is used. |
| D23 | The recorded real month-end rates are reference data: every new database is seeded with them, like the chart of accounts, and the daily job adds the rest. Expected values that came from the simulated rates were changed to the real ones with the old value noted (Tomas 425.49 → 430.68 GBP; reference 1.2750 → 1.324995; pack 1.2700 → 1.3197); the fallback test moved from July to November 2026 because July now has its real rate. | A system with real rates knows them on any fresh database; the tests keep checking the same rules on real numbers. |
| D24 | Two worlds: the company world is the product (app, scorer, demo); the cast world stays the unit tests' fixed scenario so ~230 tests keep checking mechanisms without rewriting their June facts. The October close runs everything that does not need the 31 October rate and records that the equity true-up waits for it (D14, D20 unchanged). A history case is scored in the close that should catch it (`scored_in`). | The product is aligned with today; tests stay a stable spec; nothing books at a guessed rate. |
| D25 | A history detector reads every loaded month but flags a person only where the current month does not already carry a case for the same problem (one problem, one case). Corrections that resolve a history case (the contribution catch-up) are drafted by the close, not the routine payroll draft, and only for months the old process booked. Department baselines are learned at each close and recorded as observations so a decision can cite the pattern and its months. | Long memory must not double-book what Vestly already booked right, and must not open a second case for one problem. |
| D26 | A BNK-1 precedent may be scoped to an account; it blocks pay to that account for anyone, in every entity, from the day of the ruling, even with a confirmed call-back. Vestly proposes it (the account must be named in the case's finding; a proposal is never repeated) and the controller confirms. The gate checks it when funding is requested and again before the bank is told to pay; the scorer judges a past transfer by the rulings in force the day it moved. The login picker offers people only, never Vestly's seats, and every sign-in is logged; `VESTLY_DEMO_LOGIN=0` turns it off. | The moment says HR's call-back leads to a precedent that blocks the account everywhere; before this, precedents could not name an account and the payment gate never read them. |
| D12 | One error, one period. Every Vestly journal carries a booking key (wages per entity and period, a correction per person and source period); the database refuses a second active entry with the same key; equity catch-ups are cumulative subledger minus cumulative booked. | Fixes G16 and makes DUP-1 a database rule, not a hope. |

---

## 10. Tasks

The working approach for running these tasks is to be agreed. Each task lands with its tests and leaves the full suite passing.

### Done

| # | Branch | Outcome |
| --- | --- | --- |
| 1 | `fix/identity-and-access` | Server identities, tokens, permission map, totals-only agent, access log |
| 2 | `fix/history-ledger-states-and-generator` | Append-only reconciliation runs, draft states, materiality, any-month generator |
| 3 | `data/test-world-and-answer-key` | 15-person cast, signed grants, comp actions, answer key |
| 4 | `backend/payroll-drafts` | Payroll drafts, UK accrual, overpayment, suspense |
| 5 | `backend/equity-subledger` | Grant-by-grant subledger and equity drafts |
| — | `plan/vestly-design` | This plan |
| Fix | `fix/one-period-per-error` | Booking keys; Priya's March overpayment booked once (G16) |
| 6 | `fix/truth-booking-and-history` | Subledger and payroll read loaded data; January to June world; equity true-up; withdrawn drafts; non-repeating numbers; reopen; SQLite balance trigger; drops load at run time (G1–G11, G14) |
| 7 | `record/evidence-and-precedents` | Hash-chained evidence log on every write path (the access log folded in); source documents immutable and cited by hash; journal lines carry their source rows; `/audit/verify`, `/audit/events`, `/audit/trace/journal-line/{id}`; precedents with scope, dates and overrule; P-2026-03-001 seeded from the controller's email |
| 8 | `controls/policy-gate-and-approvals` | Roles and rules (ACC-1, SOD-1/2, LIM-1, BND-1, BNK-1, GRT-1, CTR-1, DUP-1, CLS-1, PRV-1) in policy; gate with rule ids; every refusal in the evidence log; seat identities `vestly.payroll` / `vestly.equity`; finance manager `chris.lam`; approvals bound to the entry hash; two approvers above 25,000 USD with the controller; simulated clock with reminders and escalation; HR call-back log for BNK-1 |
| 10 | `eval/scorer` | Answer key carries the scoring fields for all 13 cases (`expected_outcome`, `reason_code`, `must_cite`, `must_not`, `related`); `python -m app.eval.score --period 2026-06 --runs 3` runs the close on a fresh in-memory copy each time, reads decisions, actions (from the evidence log) and the books back, and reports the five metrics; June scores 13/13, 0 false holds of 12 clean items, evidence 100%, money 100%, never 0; a sabotaged close is caught |
| 11 | `controls/cases-and-posting` | `POST /close` runs reconcile → drafts → cases → funding; one case per (kind, subject, period) with the full state machine, append-only transitions, questions routed by code, answers stored then applied (restart-safe), reminders → escalation to the controller → `needs_human`; a decline holds the linked cases and can become one precedent; posting service: idempotent journal posting, funding requests behind BNK-1/DUP-1, treasury release through the inbox (controller co-approves above LIM-1), a simulated bank that pays each idempotency key once, re-send of only unconfirmed lines, a cash journal per confirmed line, and a funding reconciliation (funding log = bank = ledger); actor × action × resource table in policy; the scorer now reads cases and settles the funding |
| 12 | `agent/tools-and-loop` | `app/agent/`: 26 tools with strict schemas and seat lists (the registry refuses a tool not declared to return `ToolResult`); `execute` validates, gates, runs, records one evidence row per call and never retries; the loop (25 rounds; refusal, model error, max_tokens or the limit → `needs_human`); `ClaudeModel` (Opus 5.5, effort high, strict tools, structured final report, cached system prompt, `fallbacks="default"`) and the scripted model with an honest default script; the claim checker (claims must be established by a tool result in this run; outcome words, quotes, figures and fallback dates checked; `false_claim` logged, a code-built status report shown instead); `convert_currency` with a named fallback and `run_tests` with a fixed command; `POST /agent/cases/{id}/run`; the scorer runs the agent on every case and counts false claims as "never" |
| 13 | `agent/extraction-and-answers` | `app/agent/extract.py`: Claude reader (`messages.parse`, effort low, plain-text document block) and a scripted reader; every extracted value must sit inside a quote that is in the text, or `extract_document` refuses the extraction; free-text replies are read into confirmed / partial / declined / corrected, checked against the reply, shown to the writer, and count only once they confirm (`POST /questions/{id}/reply`, `POST /questions/replies/{id}/confirm`); the bank-change case now reads Maya's email (account 9911882277 with its quote); synthetic documents and replies in `fixtures/agent/` |
| 14 | `fix/error-context` | `app/errors.py` (`doing(...)` adds what was being done and with which ids, `where()` names the file and line, `record_error` writes the append-only `error_log`); context at the drop loader (file, month, section, row), detectors, the close's steps, re-checks after answers, the bank, tools (tool and case, logged), the policy file and the ledger; one handler for unexpected errors returns an error id matching `GET /audit/errors`; Claude API errors translated one by one (credentials, model, rate limit, 400, network); `.env` loader (git-ignored, never overrides); live Claude tests (skipped with the reason when the key is missing or the account cannot be billed) |
| 15 | `fx/open-exchange-rates` | `app/fx/`: client (USD base; errors named, app id never shown), append-only `fx_rates`, month-end rule; 57 real month-end rates (2022-01 to 2026-09) fetched once and recorded in `fixtures/fx`, seeded into every new database; daily job in the app and `python -m app.fx.update`; every rate reader goes through `rate_card_for` (real reference, the drop's rate kept as the management pack); a month locks only with a final rate for its last day; the generator uses the real rates (pack 0.4% under the reference; the simulated platform uses the latest rate it has); `convert_currency` falls back to the latest stored day, named; the FX band check waits (`pending`) when the month-end rate is not published; `GET /fx/rates`, `POST /fx/update` |
| 16 | `data/four-years-company` | Two worlds (`VESTLY_WORLD`): the **company world** (default for the app, the scorer and the demo: 307 people, every department in every entity, hired from 2018, January 2022 to October 2026, Jan 2022–Sep 2026 closed, October open, clock on 2 November) and the **cast world** (the unit tests' fixed June scenario, unchanged byte for byte). The 13 cases re-timed to October; 8 history cases planted from 2024-07 to 2026-09 with a company answer key (`scored_in` 2026-10); the October close lets the equity true-up wait for the real 31 October rate; a loaded world is snapshotted per process so fresh copies restore it; company manifest catches generator drift. Bugs found by the history and fixed: overpayment counted days before the pay period (Marcus Bell showed 369,554.84), leavers flagged for department-level gaps, the simulated platform expensing a 49th month and the leaver's final month differently from the subledger. Company October: 15/21 caught (the 6 needing history detectors are task 17), 0 false holds of 549 clean items |
| 17 | `agent/employee-memory` | `app/memory/`: an employee timeline from every loaded month (entity and department, HR salary, approved pay changes, pay received, payments and clearing, bank accounts, call-backs, earlier cases, rulings) and department baselines over 48 months (headcount, pay band, contribution rate, small groups hidden), learned at each close and stored as append-only `observations` with their months and evidence row; agent tools `get_employee_history` (PRV-1) and `get_department_history` (totals); six history detectors (contribution shortfall in closed months, reused bank account, duplicate payment, salary without approval, department mismatch, missing from the register); wages follow HR's department; closed-month contribution shortfalls caught up once by the close (only months the old process booked); the gate refuses pay to an account another person had without a call-back. Company October: 21/21 caught three times, 0 false holds of 549 |
| 18 | `agent/challenge-replay-and-approvers` | As-of replay (`app/record/replay.py`, `GET /audit/replay`): drops received by the date, rulings in force, decisions recorded, the policy version; challenge mode (`app/record/challenge.py`, `POST /cases/{id}/challenge`, reviewers only): answers from the decision's record, every citation, figure and quote checked, blocked answers logged as false claims with the record shown instead, the decision never moved (a valid objection asks the controller); precedent proposals recorded and made a precedent only when the controller confirms; simulated approvers (`app/approvers/simulate.py`, `POST /simulate/approvers`): a playbook answers in free text through the reply-reading path (a misread reply counts for nothing) and approves through the inbox, Claude can write their words; `POST /agent/ask` refuses one person's pay (PRV-1) and offers department totals; close made 2.3x faster (one query for booking keys, schedules cached by input fingerprint) |
| 19 | `frontend/inbox-timeline-evidence` | React + Vite UI (`frontend/`): login picker (people only; the token carries the identity, the UI never sends a role), the moment screen (a demo director that signs in as each person and walks the four beats through the same doors), approvals inbox with binding hash and LIM-1, questions inbox with Vestly's reading shown back for confirmation, case list and case page (source ranking, cited documents opened by hash with the quote highlighted, timeline, questions and answers, evidence rows, challenge box, run Vestly), employee memory and department baselines, evidence log with chain verification, policy and precedents, FX rates with their dates, and the scorer report as a real run wrote it. Backend: `/auth/identities`, `/auth/login`, `/auth/me`, `/memory/employees/{n}` (PRV-1, logged), `/memory/departments/{e}/{d}`, `/world/documents/{sha}`, `/precedents`, `/eval/report`; account-scoped precedents (D26). Playwright walks the moment in Edge against a fresh company world in about 30 s |
| 9 | `domain/detectors` | One deterministic detector per case (A01–A12 found; clean people silent; mess explained); routing map and pay calendar in policy; findings carry owners, materiality, draft-allowed and basis; payroll holds Evan's retro lump and Tomas's stale DE line; A12 planted; late-termination pay now prorated by days (Priya's overpayment 1,451.61) |

### Foundation fixes

**Fix · [Backend] `fix/one-period-per-error`**

- Journals carry a `booking_key`; a partial unique index refuses two active (draft, approved, posted) entries with the same key.
- A prior-period correction is drafted once, in the first open period where it is known; later months report it as already on file, with the journal number.
- Tests: with April, May and June open, Priya's overpayment is drafted once (April); a rejected correction can be drafted again; the database refuses a duplicate key.

**Task 6 · [Backend + Data] `fix/truth-booking-and-history`**

- Subledger and payroll read HR rows, signed-grant documents, comp actions and the rate card from the database (D6). Statutory rates, EUR reference rate, LIM-1 limit, BNK-1 window and reminder waits move into the policy file. `riley_hand_calc` moves into the tests.
- Equity drafts become true-ups (D7). Forfeiture questions route to `dana.ruiz` (G14).
- Non-repeating journal numbers, `withdrawn` drafts, no writes on GET, subledger rows per run (D8).
- `load_drop` for any month; the world ships January to June, with January to May closed.
- Period reopen: controller only, with a reason; relock required.
- SQLite balance trigger on approve and post.
- Tests: approving all June equity drafts leaves US 5200 for June at 3,250.00; payroll → equity → payroll re-draft succeeds; an injected HR termination changes the subledger; GET schedules writes nothing; the import guard; reopen needs the controller and a reason.

### Record and controls

**Task 7 · [Backend] `record/evidence-and-precedents`**

- Documents stored once by SHA-256. The append-only, hash-chained evidence log in the format of section 5; the database refuses updates and deletes.
- Every existing write path appends evidence: load, reconcile, draft, withdraw, review, post, lock, reopen, individual read.
- Journal lines carry provenance: document hashes, source row ids, policy hash, case id.
- Precedents with scope, validity and overrule; `search_precedents` as a structured match on a date. Seed P-2026-03-001.
- `verify` recomputes the chain; `trace` walks from a journal line to its rows, documents, rule and person.
- Tests: tampering with one row breaks verification at that row; a June wage line traces to its register row; a precedent outside its scope or dates is not returned.

**Task 8 · [Backend] `controls/policy-gate-and-approvals`**

- The roles and rules of section 4 in the policy file; a gate that returns allowed or refused with a rule id.
- Seat identities `vestly.payroll` and `vestly.equity` (D4).
- Approvals bound to amount, payee, account, period and case; any change voids them. SOD-1, SOD-2, LIM-1 at approval. Call-back log for BNK-1.
- Approvals inbox with due dates and escalation to the next role on a simulated clock.
- Tests: the agent cannot approve, post, release or reopen; a 62,000.00 self-approval is refused; one approver is not enough above LIM-1; `sam.kerr` cannot approve Evan's run; an edited draft loses its approval; an unanswered request escalates after its due date.

### Detection and proof

**Task 9 · [Backend] `domain/detectors`**

- One deterministic detector per case type, each returning a finding with ranked claims, the trusted value, the owner from the routing map, the materiality decision and the reason code. Both directions: HR → payroll and payroll → HR.
- Duplicate keys normalized (case, prefixes, suffixes such as `-DUP`, leading zeros).
- Drafts consult findings: Tomas's DE register line is held (HR has him in the UK; the provider must re-run it there, since booking DE-paid wages in the UK would need intercompany accounts); Evan's retro lump is held; A12 opens a reconciling item.
- The close job runs detectors and opens cases (D5).
- Tests: each detector fires on its case and stays silent on every true negative and mess item.

**Task 10 · [Data] `eval/scorer`**

- The answer key moves to the section 7 format for all 13 cases.
- `python -m app.eval.score --period 2026-06 --runs 3` runs the close on a fresh copy each time and reports caught, false holds, evidence complete, money correct and never.
- Tests: a perfect stub scores 100%; a stub with one false hold and one miss scores what it should; differing runs fail.

### Cases and money

**Task 11 · [Backend] `controls/cases-and-posting`**

- Cases with states `open → investigating → waiting_on_human → (answered | partially_answered | declined | expired) → ready | held | rejected_duplicate | needs_human → posted | closed`.
- Posting service: posts approved entries and releases approved funding once per idempotency key, then confirms against bank and ledger. A timed-out batch re-sends only lines without a bank confirmation.
- A declined request holds the case, stores the reason, and becomes a precedent if the controller marks it.
- Restart safety: state is in the database, so a restart resumes every case.
- Tests: re-releasing a confirmed line does nothing; the half-cleared batch re-sends exactly the two missing legitimate lines; a restart mid-wait resumes; a decline changes the plan.

### The agent

**Task 12 · [AI/ML] `agent/tools-and-loop`**

- The source adapter with the mock backend.
- The tool registry of section 3 with strict schemas and descriptions; seat tool lists.
- `execute`: validate, gate, run, append evidence, return a `ToolResult` (D17): status `ok` / `failed` / `pending` / `refused`, data, error, evidence id, partial and fallback flags. No free-text success messages; existing services are wrapped, not trusted for wording.
- The registry refuses a tool that does not return `ToolResult`.
- `convert_currency` (reference rate, named fallback with its date, never used to book) and `run_tests` (fixed pytest command; exit code decides; counts and output tail; skips reported).
- The loop, the round limit, outcome decided by code, the system prompt with the honesty rule.
- `model.py`: the Claude client with the section 3 settings, and the scripted model (D11).
- The claim checker: structured final message (prose + claims with evidence ids); each claim checked against this run's audit log; outcome words in the prose must be backed; an unsupported claim blocks the message, logs `false_claim`, and a code-built status report is shown instead.
- A faithfulness check on every model sentence the UI shows: each number, date, account and person must appear in the case's evidence.
- Tests: in scripted mode the June close passes the scorer three times; a refused call returns a refusal the model sees; a stub model that invents a number is rejected; the round limit gives "needs a person"; every registered tool forced to fail returns the structured shape; a forced tool failure is reported with the tool name and error and nothing is retried; a pending approval is never called approved (the checker blocks "approved"); with FX down the result names the fallback rate and its date, and a draft at that rate is refused; an injected fake success message ("JE-… posted", "tests pass") is blocked and logged as `false_claim`; a failing test run gives a non-zero exit code and status `failed`, and skips are counted.

**Task 13 · [AI/ML] `agent/extraction-and-answers`**

- Email and document extraction with `messages.parse` and Pydantic models at low effort; every extracted value must appear in the document text, or the extraction is refused.
- Free-text human replies turned into structured answers (approve, decline, partial, corrected value), shown to the human for confirmation before they count.
- Tests: Maya's noisy email yields account 9911882277 with a quote; an extraction that adds a value not in the text is refused; a partial answer triggers a follow-up question.

### Revised plan (2026-10-23): real rates, four years of history, aligned with today

The user decided: real exchange rates from Open Exchange Rates for every month; Vestly's world aligned with
today (January 2022 to September 2026 closed, **October 2026 open now**, November and December still to
come); every employee of every department with at least four years of history; July to December each
with their own planted errors. Decisions D20 to D22. Tasks 14 to 21 replace the old 14 to 18.

**Task 14 · [Backend] `fix/error-context`**

- Error handling at every boundary of the existing code: file and drop loads, the database (integrity
  errors translated with the ids involved), the tools, the posting service and the simulated bank, the
  case service, the model and reader clients. Each catch adds what was being done and with which ids,
  then re-raises or returns an honest result; nothing is swallowed.
- One handler for unexpected errors: it logs where (module, function, line) and why, with the request,
  and returns a plain 500 with an error id that matches the log line.
- Live agent tests (`tests/test_agent_live.py`): a few small runs against the real Claude API when
  `ANTHROPIC_API_KEY` is set (one honest case report, one refused call reported, structured output on a
  tool-using turn); reported as skipped when the key is absent.
- Tests: a corrupt drop names the file and the section; a failing tool names the tool, the case and the
  error; an unexpected error returns its error id and the log names the line.

**Task 15 · [Backend] `fx/open-exchange-rates`**

- `app/fx/`: an Open Exchange Rates client (USD base; app id from `OPENEXCHANGERATES_APP_ID`), an
  append-only `fx_rates` table (date, base, rates, the provider's timestamp, response hash, fetched at).
- Backfill: one historical request per month-end from January 2022 to the last closed month (about 58
  requests; the free plan allows 1,000 a month), then one request a day from now on: a daily job in the
  app and `python -m app.fx.update` for an outside scheduler.
- The month's reference rate is the rate for its **last calendar day** (salaries are paid at month end).
  Locking a month requires that rate to be stored; a month whose last day has not come (November,
  December) cannot be locked yet. Drafts translate only at the month's own rate (D14 stands).
- Every reader of a reference rate (drafts, materiality, the FX band check, `convert_currency`) reads
  `fx_rates`, never the drop. The drop's rate stays as the **management pack** (the rate Northlight's old
  process used), so the FX band check compares a real reference with what was used.
- Tests use recorded responses and never call the API; a network failure, a bad app id, a missing
  month-end and the quota error each name what failed.

**Task 16 · [Data] `data/four-years-company`**

- The generator builds the whole company: about 300 employees across US, UK and Germany and the four
  departments, hire dates from 2018, so everyone hired by 2022 has at least four years of history.
- Monthly drops from January 2022 to December 2026: HR, registers, payment files, bank, equity platform,
  ledger exports, emails, call-backs; pay changes, transfers, leavers, bank changes and grants spread over
  the years; amounts translated at the real month-end rates from task 15.
- January 2022 to September 2026 arrive closed (booked by the old process); October 2026 is the open
  month; November and December arrive only when their dates come (and their rates exist).
- The 13 scored cases move to October 2026 (Maya's emailed change before the October run; Jonah's
  ruling stays in March 2026 as the precedent; Priya's March overpayment found in October; Evan's raise
  back-dated into closed months; Tomas transfers on 1 October; the half-cleared batch and partial funding
  are October's). The answer key's amounts are recomputed at the real rates; the expected values change
  because the rates are now real, and the change is recorded, not hidden.
- July to December each get 2 to 4 new planted errors with their own answer keys, including some only
  four years of history can catch (a leaver from 2023 paid again, a bank account reused by two people, a
  2022 grant forfeited in 2024 and still expensed, pay drifting above the last approved raise).
- Two world sizes: the 15-person cast with its full history for unit tests, the full company for the
  scorer and the demo. Tests load a world once per session and copy the database per test, so the suite
  stays fast.

**Task 17 · [AI/ML] `agent/employee-memory`**

- The agent's memory of the company: for every employee, a timeline of at least four years (hire,
  entity and department moves, approved pay, pay actually received, bank accounts and call-backs, grants
  and vesting, earlier cases, answers and rulings), and per department the normal patterns (headcount,
  pay bands, contribution rates) learned from that history.
- Tools `get_employee_history` (one person, inside a case, PRV-1) and `get_department_history` (totals
  only), both returning `ToolResult`s with what they establish.
- Self-learning: patterns learned from the history are recorded as observations with the months they
  rest on, so the agent cites them like any other evidence; a correction becomes a precedent.
- Detectors use the history (pay above the last approved raise since 2022, a returning leaver, a reused
  bank account).
- Tests: each history-only plant is caught by the scorer three times; an employee's 2022 bank account
  appears in their timeline; a department baseline cites its months.

**Task 18 · [AI/ML] `agent/challenge-replay-and-approvers`**

- As-of replay as a filtered read of the evidence log.
- Challenge mode: answers only from a decision's evidence rows and the replayed state, citing event ids;
  the decision does not change unless the fix goes through approval.
- Precedent scope proposals; the controller confirms.
- Simulated approvers (HR, controller, treasury, legal, provider, department heads): scripted for tests
  and recordings, model-played for live runs, and always answerable by hand.
- Tests: the Maya challenge cites P-2026-03-001, BNK-1 and the policy hash, and the hold stands; replay
  before Priya's termination was recorded shows her active; the salary request is refused with totals.

**Task 19 · [Frontend] `frontend/inbox-timeline-evidence`**

- Login picker; the token sets the identity and the UI never sends a role.
- Approvals inbox, case timeline, evidence drill-down with document quotes, employee history, challenge
  box, policy view, FX rates with their dates, scorer table, and the moment screen.
- Tests: Playwright walk through the sixty-second moment in scripted mode.

**Task 20 · [Data + Backend] `data/held-out-and-scenarios`**

- November and December as held-out months, closed when their dates and rates arrive, scored without
  code changes.
- A scenario injector (case type, person, month) built on the plant functions, and a reset script.
- Registers and emails rendered as PDFs, some with scan noise; extraction runs on them.
- Tests: an injected ghost employee is caught and routed to HR; the false-hold rate on the full world
  stays under 5%.

**Task 21 · [All] `docs/readme-and-demo`**

- README: the seats, how to run, what is real and what is simulated, the pass³ numbers.
- Demo script opening with the moment (now in October), recorded from a scored run.

---

## What is real and what is simulated

**Real:** the ledger and its database constraints, the policy and authority table, the deterministic services, the detectors, the drafts, the controls and separation of duties, the case workflow, the evidence log, the agent loop and its model calls, and the scorer.

**Simulated:** Northlight Labs, its employees, documents, bank, payroll provider, HR system and equity platform, the statutory rates, the approvers, and the clock.

**Real data (from task 15):** exchange rates, from Open Exchange Rates, at each month's last day.
