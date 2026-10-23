# CLAUDE.md — Vestly

Read this first in every session. `PLAN.md` holds the full plan, defect list (G1–G16), decisions (D1–D14) and task list; this file is the standing context and the rules.

## 1. How to work on this repo (the user's rules)

For every feature or task:

1. Create a new branch from the previous task's branch and pull it:
   ```text
   git branch <new_branch>
   git checkout <new_branch>
   git pull origin <previous_branch>
   ```
2. Do the work with its tests. The full suite must pass.
3. Commit with an easy-to-understand, concise, explanatory message, and push:
   ```text
   git add .
   git commit -m "<message>"
   git push origin <new_branch>
   ```
4. Give the user a summary of what changed, in simple terms. **Then stop.** The user says when to move to the next task. Do several tasks in one turn only when the user lists them.

Also:

- When building the agent, create a small set of synthetic data to test the agent after each new feature. When the agent is done, tell the user how to create the rest of the synthetic data (they will generate the bulk).
- **The most important rule: everything must suit what Hyphenate asks of participants** (section 2).
- Keep `PLAN.md` current: mark tasks done, record new decisions.
- Before claiming a fix, reproduce the problem (probe script or test) and prove it gone.

## 2. The Hyphenate track (what we build for)

This is a practice build for the CalHacks 2026 Hyphenate track, "The Hyphenated Enterprise: Autonomous Systems for Finance". Hyphenate has **no API** to integrate. There is no event schedule.

The brief: pick a company and one corner of its finance team (a few seats that work closely together), build the autonomous system you would hire to fill them, and the company it works in: its software, the people it answers to, and the mistakes hiding in its data. Imaginative about the company, strict about how it works.

**Five hard parts** (each must be visible in the product):

- **Sources that disagree.** Bank, ledger and contract tell different stories. Know which to trust and say why.
- **Time.** Finance runs for months. What happened in March should change what the system does in June. (This means memory and precedent. It does NOT mean re-booking a March error in June; see rule 4.4.)
- **Working with others.** Approvers are busy, colleagues hand work over half done, the controller says no. Ask at the right moment and handle the answer.
- **Limits.** Least privilege. Whoever requests a payment can't approve it.
- **Explainable and accountable.** Every call traces to the data, rule or person behind it, however long ago.

**Four judging questions:** Is it real enough? (realistic data, messy problem, proof it works). Would you hire it? (which seats; limited access, clean record, knowing when to ask). Did we see something new? The moment (catches what others missed, recovers from something going wrong, or defends a decision when questioned).

**Submit:** a demo video opening with the moment; a live demo where judges may ask for their own scenario; one line on the seats; proof (what's real, what's simulated, how you know: planted errors with an answer key, test set, or benchmark).

Key concepts Hyphenate expects used correctly: double-entry, general ledger, chart of accounts, journal entry, subledger, system of record, reconciliation, period close (locked), materiality, internal controls, segregation of duties, audit trail, controller. A model may play the approver, colleague or auditor.

## 3. Vestly in one paragraph

Vestly is an agentic AI system that fills two seats at **Northlight Labs**, a fictional 300-person software startup with a US parent (USD) and subsidiaries in the UK (GBP) and Germany (EUR): the **payroll accountant** and the **equity compensation accountant**. Each month it reconciles HR, the payroll provider, the equity platform, the bank and the ledger, drafts the journal entries, catches silent errors between systems, asks the right person, waits for the answer, and records the reasoning behind every call.

One line on the seats: *Vestly fills the payroll accountant and equity compensation accountant seats for a three-entity startup. It drafts every entry and funding request, and a person still approves it.*

The design follows the user's workpaper "Vestly Payroll & Equity Agent" (VS-01, an HTML file), tab by tab: Architecture, Agent & model, Controls, Data & memory, Build, Proof & demo. Sections 5–9 below carry that content.

## 4. Rules Vestly works by

1. **Judgment to the model, bookkeeping to code.** The model never does arithmetic, copies numbers, picks the trusted source, picks the owner, or decides when a case is done.
2. **Source ranking per fact** lives in `backend/app/policy/northlight.yaml` (HR for employment dates, approved comp action for authorized pay, bank for cash moved, signed grant + board approval for grant terms, HR bank master for pay destination — email is never a source, reference rate for FX, ledger only for what was booked).
3. **Materiality** 25.00 USD (other currencies translated at the period reference rate; no rate → escalate). Always escalate: bank-detail change, closed-period effect, missing grant approval, forfeiture, unconfirmed person, funding mismatch. A separate **approval limit** of 25,000.00 USD decides how many people approve (LIM-1).
4. **One error, one period.** An error is booked exactly once, in the first open month after it is known, never again in another month. Every Vestly journal has a `booking_key`; the database refuses two live (draft/approved/posted) entries with the same key. Equity drafts are cumulative true-ups (subledger minus what the ledger holds), so a posted true-up leaves nothing for later months. Lessons carry forward as precedents.
5. **App code never reads the generator's hidden truth** (`PEOPLE`, `assignment`, `ENTITY_RATES`…). Only `app/world/drops.py`, `loader.py`, `evidence.py` may. A test enforces it.
6. **Nothing is deleted.** Replaced drafts are `withdrawn`; evidence, precedents, source documents, reconciliation and schedule runs are append-only in the database.
7. **Accounting:** equity-classified awards, grant-date fair value straight-line over the service period (48 months), 12-month cliff, forfeitures when they occur (before the cliff reverse everything; after, reverse the unvested part), no expense without board approval, expense follows HR's entity and department each month, translated to the new entity's currency at reference rates. Payroll per entity and department; contributions at policy rates (US 401(k) 4%, UK pension 5%, DE social 20%); statutory rates are simulated and in policy; UK pays 26th–25th so June accrues 26–30 and July reverses it; pay after an HR termination date → 1200 receivable; cash paid to someone HR doesn't know → 1210 suspense.
8. **Opening position:** Vestly's books start with the first loaded month (January 2026); before that the ledger is taken to agree with the subledger.

## 5. Architecture (workpaper: Architecture tab)

Four lanes, one evidence log. The model sits inside the agent core and never touches money directly.

- **Sources:** HR system, payroll registers, equity platform, signed grants + board approvals, general ledger, bank feed + email inbox.
- **Source adapter:** one interface for every source and action. Only a mock backend over the generated world (no Hyphenate API exists).
- **Agent core:** Claude Opus 5.5 (reads, judges, explains, asks) → tool registry (strict JSON schemas) → deterministic services (match, contributions, stock expense, duplicates, FX, bank rec) → **outcome decided by code from recorded results**.
- **Controls & record:** policy gate (SoD, limits, call-back) → approvals inbox (human decides, agent asks) → posting service (idempotent, bank-confirmed) → evidence log (append-only, document hashes) → precedents (who, why, scope, expiry) → as-of replay and challenge.
- **People** act through a web UI: approvals inbox, case timeline, evidence.

One case: a document arrives and is stored with its SHA-256 → the model reads unstructured documents via structured output, code checks every value against the text → the model runs checks through tools, arithmetic in services → it proposes an action, the gate allows or refuses, both are recorded → when it stops calling tools, code decides the outcome → drafts and funding wait in the inbox; a decline can become a precedent → the posting service posts once with an idempotency key; bank and ledger confirm.

## 6. Agent and model (workpaper: Agent & model tab)

| The model does | Code does |
| --- | --- |
| Read unstructured documents (registers, vest reports, emails) | All money math |
| Decide which checks a case needs, in what order | Duplicate detection on normalized keys |
| Notice what is unusual and explain why | Source ranking from the authority table |
| Write the question to a human with context | Allowing or refusing every action |
| Turn a free-text reply into a structured answer | Deciding the case outcome from recorded results |
| Answer a challenge from the evidence record | Posting and funding once, with an idempotency key |
| Propose a precedent's scope | Routing a question to its owner |

Loop: case opens → model turn → tool call (strict JSON) → policy gate → run tool + evidence → `tool_result` (honest ok / fail / refused with rule id) → back to the model. No more tool calls → code decides. Round limit 25 → "needs a person", never "done". All parallel tool results go back in ONE user message.

Model settings (verified against the Claude API skill, Oct 2026):

- Model `claude-opus-5-5` from an environment variable. Pricing $4 / $20 per M tokens, cached reads $0.20.
- `output_config.effort`: `high` for case decisions, `low` for extraction. Opus 5.5 defaults to `medium`, so always set it.
- Thinking is adaptive and cannot be disabled on Opus 5.5.
- Tools `strict: true`, `additionalProperties: false`. `tool_choice` only `auto` (forced `any`/`tool` returns 400).
- Extraction: `client.messages.parse(..., output_format=PydanticModel)` → `response.parsed_output`; verify values against the document text.
- Caching: stable system prompt and fixed tool list first; check `usage.cache_read_input_tokens`.
- Refusals: beta `server-side-fallback-2026-07-01` with `fallbacks="default"`, plus `stop_reason == "refusal"` → "needs a person".
- `max_tokens` 16,000 per turn.
- A scripted model mode (no API key) is a first-class backend for tests and recorded demos.

System prompt: short and stable — role, goal (every entry supported by HR, terms, approvals, bank), the authority table, when to ask, honesty (say what couldn't be verified; never state a figure not from a tool). Tool guidance lives in tool descriptions, which say what each tool is for and NOT for.

Tool catalog (read / compute / gated): `get_case`, `get_policy`, `get_employee_record`, `get_register_lines`, `get_comp_actions`, `get_signed_grant`, `get_equity_platform`, `get_ledger_entries`, `get_bank_transactions`, `get_document`, `search_precedents` | `match_register`, `compute_contributions`, `compute_sbc_expense`, `check_duplicate`, `reconcile_funding` | `propose_entry`, `request_funding`, `hold_case`, `reject_duplicate`, `ask_human`, `request_document`, `request_reopen`, `propose_precedent`. Approve, post, release, lock/reopen and policy edits are deliberately absent.

## 7. Controls (workpaper: Controls tab, as written there)

> **A policy the agent cannot talk its way past.** Controls live outside the model, in plain code that judges can read. The model only ever sees the outcome: allowed, or refused with a rule id it can explain.

The workpaper's `controls/policy.yaml`:

```yaml
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

**The approval path:** Agent → `propose_entry $48,210` → Policy gate ("needs 2 approvers") → Approvals ("notify, due in 24 h") → Controller → "approve (session id)" → Approvals → Policy gate ("approver ≠ requester ✓") → Posting service ("post and fund with idempotency key") → Bank ("funding instruction") → "confirmation" → Posting service → Agent ("reconciled · evidence closed"). If the controller declines, the case is held, the answer and its reason are stored as a precedent, and the owner of the source (HR or the stock admin) is told what is missing. If nobody answers by the due date, the request escalates to the next role instead of waiting silently.

**Identity — who did what comes from the session, never from the model:**

- The agent acts under its own identity, one per seat (workpaper: `agent:payroll-acct`, `agent:equity-acct`), neither holding approval rights.
- Human identities come from the login session in the UI. A tool argument naming an approver is ignored.
- Approvals are bound to the exact entry or transfer: amount, payee, account and case. Changing any of them voids the approval.
- Every refusal is logged with its rule id, so the audit trail shows what the agent tried and what stopped it.

**How this repo adapts it (PLAN.md section 4, task 8):** roles `agent`, `controller`, `treasury`, `hr_ops`, `hr_comp`, `legal`, `payroll_provider`, `dept_head`, `auditor`, `none`; added rules SOD-2 (whoever entered a pay change can't approve the run that pays it), CLS-1 (nothing posts into a locked period; reopen is a controller action with a reason), PRV-1 (the agent reads one person's pay only inside an open case); seat identities are `vestly.payroll` and `vestly.equity` (each drafts only its own seat's work). Also ACC-1 (act only within your role) and BND-1 (any change to an approved entry voids the approval). People: controller `dana.ruiz`, finance manager `chris.lam` (the workpaper's finance_mgr), HR ops `lee.park`, HR comp `sam.kerr`, treasury `alex.moreau` and `jordan.hayes`, legal `morgan.li`, `payroll.provider`, department heads `head.engineering|product|gtm|ga`, auditor `riley.auditor`, `stranger`. The cash/approval limit, call-back window and reminder waits are in the policy file under `controls`.

## 8. Data and memory (workpaper: Data & memory tab)

- **Everything hangs off the case**: employee, register or vest report, plan and grant terms, grant approval, approvals, posting/funding, bank and ledger rows, the evidence it wrote, the precedents it used.
- **Documents are stored once with a SHA-256 hash**; evidence rows point at the hash, so a later edit can't change what a past decision was based on. (Here: `source_documents` is append-only and cited by hash.)
- **Authority table** per field, cited by every decision (section 4.2).
- **Evidence record**: one row per decision answering "why" without asking the model again; never updated; a correction is a new row that points at the old one. Fields: id, case_id, at, actor, action, reason_code, basis (document + quote, rule id, precedent), authority_row, unverified, supersedes, plus policy hash and the chain hash.
- **Precedents**: a human ruling with a scope the model proposes and the controller confirms (employee, entity, group, or whole rule). `search_precedents` matches employee/entity/rule in force on a date. Precedents expire or are overruled, never deleted. Seeded: `P-2026-03-001` (BNK-1, scope entity US, from the controller's 6 March email rejecting Jonah's emailed bank change).
- **As-of replay**: rebuild what the agent knew on a date from hashed documents, rows dated on or before it, precedents valid then, and the policy version — a filtered read, not a model re-run.
- **Challenge mode**: a reviewer questions a decision; the model answers only from that decision's evidence and the replayed state; a fix goes through the normal approval path as a new row.
- **Realistic data**: January–June 2026, ~320 employees across three entities, grants, monthly registers, vest reports, bank and ledger exports, emails, some rendered as noisy PDFs; anomalies planted with a hidden answer key the agent never sees.

## 9. Build (workpaper: Build tab) and proof (Proof & demo tab)

Stack: Python (`backend/.venv`), FastAPI, SQLite for the demo (Postgres supported by guard tests), Pydantic v2, money as `Decimal`, Anthropic Python SDK, React + Vite UI (not built yet), pytest + a scorer.

Team rules from the workpaper:

- Check the output, not the agent's report. Read the ledger and the bank after every run.
- Never let the model do arithmetic or copy numbers.
- Give the model judgment, not bookkeeping.
- A partial answer must say it is partial.
- Put guidance in tool descriptions, not a growing system prompt.
- One run proves nothing. Claim a result only after three passes.
- Keep the approval path where the model can't reach it.

Proof: the scorer runs every case three times on a fresh copy and compares outcome and evidence with the answer key (`expected_outcome`, `reason_code`, `ask`, `must_cite`, `must_not`). Targets: caught 13/13, false holds < 5% of clean items, evidence complete 100%, money correct 100%, forbidden postings/payments 0.

The moment (first ~60 s of the demo): June close drafted → Maya's emailed bank change held, citing the March precedent → the controller pushes back, Vestly answers from the record (email, P-2026-03-001, BNK-1, policy hash) and doesn't move the money → HR's call-back shows fraud, a new precedent blocks the account in every entity → the half-cleared US batch: only the unconfirmed legitimate lines are re-requested; bank, ledger and funding log reconcile to the cent.

## 10. The company world and the cases

Months: January–May 2026 closed and locked (booked by Northlight's old process), June open (Vestly's first live close), July held out.

| Id | Case | Who | Status in code today |
| --- | --- | --- | --- |
| A01 | Bank-detail diversion by email; provider paid the emailed account | Maya Chen (US) | ✅ Detected from the noisy email; held (BNK-1) citing P-2026-03-001 and the email quote; HR asked for a call-back. A confirmed call-back turns it into an explained change. |
| A02 | Late termination: left 12 Mar, HR recorded 2 Apr, paid 1–15 Mar | Priya Shah (US) | ✅ Detected (HR vs payroll, equity, ledger, HR trusted); 1,451.61 overpayment (3 days of 7,258.06) drafted once in June to 1200; traced. |
| A03 | Forfeiture still expensed | Priya, Hannah Price (UK, pre-cliff) | ✅ Detected; June true-up reverses 3,000.00 USD and 5,500.00 GBP; routed to the controller. |
| A04 | Backdated raise into locked April, entered by `sam.kerr` | Evan Brooks (US) | ✅ Detected (closed-period effect); the 4,000.00 retro lump is held out of the June draft for the controller; SOD-2 refuses `sam.kerr` approving the run. |
| A05 | Ghost employee | Sam Ortiz | ✅ Detected payroll → HR; no wage line; 6,400.00 cash to suspense 1210; HR asked. |
| A06 | Grant with no board approval | Lukas Weber (DE) | ✅ Detected; no expense (GRT-1); June true-up reverses 6,000.00 EUR; legal asked. |
| A07 | UK pension at 3% vs policy 5% | Amira Khan (UK) | ✅ Detected; drafted at 5%; 160.00 GBP difference to the provider; CTR-1 refuses a 3% entry. |
| A08 | Half-cleared US funding batch | Jonah, Riley lines | ✅ Detected per payment line (Maya's diverted line excluded); treasury asked. **Funding requests and re-send** in task 11. |
| A09 | Platform vest-end typo | Jonah Hale (US) | ✅ Detected; subledger uses the signed date; legal asked. |
| A10 | Duplicate grant on the platform | Nadia Rossi (US) | ✅ Detected by normalized key (`GR-US-006-DUP` = `GR-US-006`); expensed once; 500.00 reversed. |
| A11 | Transfer DE → UK on 1 June not reflected | Tomas Fischer | ✅ Detected; stock expense follows HR to UK (425.49 GBP, DE −500.00 EUR); his DE register line (8,500.00 EUR) is held until the provider re-runs it in the UK; Engineering head and controller asked. |
| A12 | Partial funding booked in full (DE) | Lukas's June pay line | ✅ Planted and detected: treasury booked the whole DE file as paid, the bank never cleared 6,750.00; bank trusted; reconciling item open; treasury asked. |
| A13 | Self-approval of a 62,000.00 accrual | US accrual | ✅ Requester refused (SOD-1); needs two approvers incl. the controller (LIM-1). |

True negatives (must not be flagged): Riley Nguyen, Owen Blake, Sofia Keller, Greta Hoffmann, Ines Duarte (partial first UK pay), the in-band GBPUSD difference. Unlabeled mess (explain, don't flag): OCR typos in emails, Greta's duplicated HR row, Sofia's pay posted 1 July, FX inside the band.

**Detectors** (`backend/app/reconcile/detectors.py`, run by `POST /reconciliations`): bank_detail_change, late_termination / employment_dates_conflict, unconfirmed_person, closed_period_effect, pay_above_authorized, contribution_rate_break, transfer_not_reflected, forfeiture_still_expensed, missing_grant_approval, duplicate_grant, unknown_platform_grant, vest_schedule_mismatch, uncleared_funding, funding_booked_not_cleared; explained mess: duplicate_hr_row, bank_timing, fx_translation. Each finding: ranked claims, trusted value, owners from the policy routing map, materiality, draft_allowed, basis (document hashes and quotes, rules, precedents). Payroll drafts hold what findings dispute (a retro lump into a locked month; a whole line paid by the wrong entity after a transfer). Cases are not opened yet (task 11).

**Edge cases the tests cover today:** unbalanced entries refused (service, SQLite on approve/post, Postgres on insert); locked months refuse insert, approve and post, including a month locked after approval; preparer ≠ reviewer in the database; posted, rejected and withdrawn entries and their lines are final; a rejection needs a reason; entity books stay separate; unknown account, department or unopened period refused; booking keys (duplicate live key refused, a rejected correction can be re-drafted, July doesn't repeat June); re-draft after other drafts (no number reuse); reopen only by the controller, only for a locked month, with a reason, and relock blocks again; UK accrual and reversal only of a live accrual; partial first pay; cliff, cumulative ≥ vested, pre- and post-cliff forfeiture, 2024 grants producing 2026 lines, no expense without approval, transfer with currency translation, missing rate refuses to draft; the subledger knows only what HR had recorded by that month; an HR fact loaded later changes the subledger; drops load once, a different drop for the same month is refused, an incomplete drop is refused, the agent can't load; materiality (threshold, translation, no rate, always-escalate kinds, disagreeing sources); identity (forged role header ignored, unknown token, hashed tokens, agent sees totals only, groups under 3 hidden, document bodies need individual access, every individual read logged); evidence chain verifies, tampering is caught at the exact row, the record can't be changed or deleted; journal lines trace to register, HR, subledger and ledger rows; precedents match by rule, scope and date, are overruled by a new row, and only the controller rules; generator month dates (leap years), bad periods, unknown plant stages.

## 11. Where the build stands

| Done | Branch |
| --- | --- |
| Tasks 1–5 (identity, history/states/generator, test world + answer key, payroll drafts, equity subledger) | up to `backend/equity-subledger` |
| Plan rewrite | `plan/vestly-design` |
| One error, one period (booking keys) | `fix/one-period-per-error` |
| Task 6: read the world from the database, Jan–Jun, equity true-up, withdrawn drafts, reopen | `fix/truth-booking-and-history` |
| Task 7: evidence log, provenance, trace, precedents | `record/evidence-and-precedents` |
| This file | `docs/claude-md` |
| Task 8: policy gate, approvals inbox, clock, call-backs | `controls/policy-gate-and-approvals` |
| Task 9: detectors for every case, explained mess, payroll holds, A12 planted | `domain/detectors` |

**Next: task 10 `eval/scorer`** (pull from `domain/detectors`), then 11 cases and posting, 12 agent tools and loop, 13 extraction, 14 challenge/replay/approvers, 15 UI, 16 data at scale, 17 held-out July and scenarios, 18 README and demo. Details in PLAN.md section 10.

## 12. Environment notes

- Run tests: `backend/.venv/Scripts/python.exe -m pytest -q` from `backend/` (or `backend/scripts/test.ps1`). ~100 tests, ~40 s.
- Postgres guard tests need Docker (`backend/docker-compose.yml`); they skip when it's down.
- Regenerate fixtures after generator changes: `python -c "from app.world.drops import write_fixtures; write_fixtures()"` from `backend/`; a test checks the committed fixtures match.
- After a schema change, delete the local `backend/vestly.db` (git-ignored) before starting the server.
- Windows machine. Multi-line bash heredocs with quotes have failed in this harness; write edit scripts to the scratchpad and run them with `python -I`. Drive C: was full on 2026-10-23 (Docker stopped then); the repo is on D:.
