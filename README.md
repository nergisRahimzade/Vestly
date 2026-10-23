# Vestly

**Vestly fills the payroll accountant and equity compensation accountant seats for a three-entity startup. It drafts every entry and funding request, and a person still approves it.**

A practice build for the CalHacks 2026 Hyphenate track, *The Hyphenated Enterprise: Autonomous Systems for Finance*. The company is **Northlight Labs**, a fictional 307-person software startup with a US parent (USD) and subsidiaries in the UK (GBP) and Germany (EUR). Each month Vestly reconciles HR, the payroll provider, the equity platform, the bank and the general ledger. It drafts the journal entries, catches the silent errors between those systems, asks the right person, waits for the answer, and records the reasoning behind every call.

## The moment (October 2026 close, about 30 seconds)

1. **The close is drafted, and Maya Chen's emailed bank change is held.** The email (`New acount: 9911882277`) is not a source for bank details. HR's bank master is. Vestly cites rule BNK-1 and the controller's March ruling **P-2026-03-001**, made when Jonah Hale sent the same kind of email.
2. **The controller pushes back** ("She has been here four years, just run it."). Vestly answers only from the decision's record: the email quote, P-2026-03-001, BNK-1 and the policy hash. Every citation, figure and quote is checked before anyone sees the answer. The hold stands, and no money goes to 9911882277.
3. **HR's call-back shows fraud.** Lee Park logs the call-back and replies in their own words. Vestly shows how it read the reply, and the reply counts only once Lee confirms it. Vestly then proposes a precedent that blocks the account; the controller confirms it. From then on the payment gate refuses that account **for anyone, in any entity**, even with a confirmed call-back.
4. **The half-cleared US batch.** Treasury releases only the two legitimate lines the bank never paid (Jonah's and Riley's), not Maya's diverted line. The simulated bank pays each idempotency key once. The cash journals post, and the funding log, the bank and the ledger agree to the cent.

The browser test walks exactly this (`npm run e2e`, below). The demo script is in [docs/DEMO.md](docs/DEMO.md).

## What makes it hireable

| Hyphenate's hard part | Where it is in Vestly |
| --- | --- |
| **Sources that disagree** | One authority per fact in [northlight.yaml](backend/app/policy/northlight.yaml): HR for employment dates and the bank master, approved comp actions for pay, the bank for cash moved, the signed grant plus board approval for grant terms, the reference rate for FX. Email is never a source. Every finding ranks the sources and says why. |
| **Time** | Four years of history per employee (January 2022 onward). March's ruling changes what Vestly does in October. Errors only the history shows are caught: a leaver paid again, a reused bank account, a raise nobody approved. Each error is booked once, in one period (booking keys enforced by the database). As-of replay rebuilds what was known on any date. |
| **Working with others** | Code routes each question to its owner. Answers in free text count only after their writer confirms Vestly's reading. Reminders escalate to the controller, a decline holds the case, and a ruling can become a precedent. |
| **Limits** | One identity per seat (`vestly.payroll`, `vestly.equity`), neither able to approve, post, release or reopen. SOD-1/SOD-2 (no self-approval; whoever keyed a pay change can't approve the run that pays it). LIM-1 (above 25,000.00 USD: two approvers, one of them the controller). BND-1 (any change voids an approval). PRV-1 (one person's pay only inside an open case). CLS-1 (locked months). The model never sees an approve tool. |
| **Explainable and accountable** | An append-only, hash-chained evidence log. Documents are stored once and cited by SHA-256. Every journal line traces to its source rows, document, rule and person. A claim checker blocks any message stating an outcome a tool result in that run doesn't show ("approved", "paid", "tests pass"…) and logs it as `false_claim`. |

## Proof

The scorer runs the close **three times, each on a fresh copy of the company**, and compares every decision, citation, amount and action against an answer key Vestly never reads. A case counts as caught only if all three runs handle it as the key expects, the same way each time.

| Close | Caught | False holds | Evidence complete | Money correct | Forbidden actions |
| --- | --- | --- | --- | --- | --- |
| October 2026 (13 planted cases + 8 found only from history) | **21/21** | **0 / 549** clean items | 100% | 100% | 0 |
| November 2026, held out | **16/16** | **0 / 555** | 100% | 100% | 0 |
| December 2026, held out | **15/15** | **0 / 557** | 100% | 100% | 0 |

Targets: everything caught, false holds under 5% of clean items, evidence and money 100%, forbidden payments or postings 0.

*Held out* means November and December were planted after the detectors were written (a bank change from a scanned PDF, German employer social at 18% instead of 20%, a payment sent twice, a leaver HR keyed a month late, a miscoded department). They were scored without changing any detector. Their answer key was corrected twice before it was final, and both corrections are recorded in the key file: one plant was missing, and December's carried items first broke rule D25.

The reports above are the files the scorer wrote: [backend/reports/](backend/reports/). The UI's *Proof* screen shows them with the command and timestamp.

Tests: `289 passed, 5 skipped` (backend, about 7 minutes). The 5 skips are the live Claude tests. They ran against the real API and skipped because the Anthropic account had no credit, not because the key was missing. The browser walk: `4 passed`.

## What is real and what is simulated

**Real:** the double-entry ledger and its database guards (balanced entries, locked periods, booking keys, preparer ≠ reviewer), the policy and authority table, the deterministic services (matching, contributions, stock-comp expense, duplicates, FX, bank reconciliation), the detectors, the drafts, the controls and separation of duties, the case workflow, the evidence log, the agent loop and its tools, the claim checker, the scorer, and **exchange rates** (Open Exchange Rates, the last day of each month, recorded once and stored append-only).

**Simulated:** Northlight Labs, its people and their documents, the bank, the payroll provider, the HR system, the equity platform, the statutory rates, the approvers (a scripted playbook, or Claude writing their words), and the clock. Scan noise is simulated in a PDF's text layer: letters are misread the way OCR misreads them. There is no image and no OCR engine.

**The model:** Claude Opus 5.5 (`claude-opus-5-5`) reads documents, decides which checks a case needs, writes questions and reports, and answers challenges. Code does all arithmetic, picks the trusted source, routes every question, allows or refuses every action, and decides when a case is done. A scripted model is a first-class backend: the tests, the scorer and the browser walk use it, so every number above is reproducible offline.

## Run it

Needs Python 3.14 and Node 24 (Windows paths shown; on macOS/Linux use `.venv/bin/python`).

```text
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m uvicorn app.main:app          # loads the company world (~40 s), then http://127.0.0.1:8000

cd ../frontend
npm install
npm run dev                                                # http://localhost:5173 (calls the backend through /api)
```

Sign in as anyone from the picker; the server decides their role. Vestly's own seats can't be picked. Start on **The moment** as Dana Ruiz (controller), or use *Play the whole moment*.

| What | Command (from `backend/` unless noted) |
| --- | --- |
| All backend tests | `.venv/Scripts/python.exe -m pytest -q` |
| The moment in a real browser | `npm run e2e` from `frontend/` (Edge; its own backend on a fresh database) |
| Score October, three runs | `python -m app.eval.score --world company --runs 3 --json reports/score-company.json` |
| Score a held-out month | `python -m app.eval.score --world company --period 2026-11 --runs 3 --json reports/score-company-2026-11.json` |
| Plant a judge's scenario | `python -m app.world.reset --scenario ghost_employee:NL-US-999:2026-10`, then restart the server. The old database is moved to `backend/runs/`, never deleted. |
| Use Claude instead of the script | set `ANTHROPIC_API_KEY` and `VESTLY_MODEL_MODE=claude` (`VESTLY_MODEL` overrides the model id) |
| Live Claude tests (cost money) | `.venv/Scripts/python.exe -m pytest -q -m live` |
| Update exchange rates | `python -m app.fx.update --daily` (needs `OPENEXCHANGERATES_APP_ID`) |

Scenario kinds: `ghost_employee`, `bank_change_by_email`, `wrong_contribution`, `duplicate_payment`, `department_miscoded`, `missing_from_register`, `vesting_typo`, `duplicate_grant`.

If a `backend/vestly.db` from an older build exists, run `python -m app.world.reset` once. It moves the old file to `backend/runs/` and builds a fresh one.

Keys go in a git-ignored `.env` at the repo root. Set `VESTLY_TOKEN_SECRET` outside a demo. The login picker is demo-only; `VESTLY_DEMO_LOGIN=0` turns it off.

## Making more synthetic data

All data comes from one seeded generator, [backend/app/world/drops.py](backend/app/world/drops.py); app code never reads it directly (a test enforces that).

- **More or different people:** change `HEADCOUNT`, `DEPARTMENT_WEIGHTS`, `PAY_BANDS`, `HIRE_YEARS` or `COMPANY_SEED`. This changes every month's drop, so regenerate the manifest (`fixtures/company/manifest.json`) and expect the October answer key's background subjects to need checking.
- **New planted errors in a month:** add plant calls to `company_plants(period)`, or to `held_out_plants` for November and December, reusing the plant functions (`ghost_on_register`, `contribution_rate_break`, `diverted_funding`, `duplicate_payment`, `paid_past_termination`, `scanned_email`, …). Add a matching entry to the answer key (`expected_outcome`, `reason_code`, `ask`, `must_cite`, `must_not`, the expected amount). Then run the scorer three times.
- **A life event after October** (a leaver, a raise): add it to `HELD_OUT_EVENTS`. It is applied after the seeded draw, so earlier months stay byte-for-byte the same.
- **Regenerate:** the held-out key with `python -c "from app.world.drops import write_heldout_key; write_heldout_key()"`, and the cast fixtures with `python -c "from app.world.drops import write_fixtures; write_fixtures()"`. Tests fail if committed fixtures drift from the generator.
- **One-off scenarios** need no code: `python -m app.world.reset --scenario KIND:EMPLOYEE:PERIOD` (repeatable).

## Where things are

```text
backend/app/
  agent/      tools (strict schemas), the loop, Claude and scripted models, the claim checker, document reading
  reconcile/  detectors: one per kind of disagreement, plus the ones that read four years of history
  cases/      the close, the case state machine, questions, replies
  controls/   policy gate, approvals inbox, clock, call-backs, posting and the simulated bank
  record/     evidence log, precedents, as-of replay, challenge mode
  ledger/ payroll/ equity/ fx/ memory/ policy/ world/ eval/
frontend/     React + Vite UI and the Playwright walk
PLAN.md       the full plan, decisions D1–D27, tasks
CLAUDE.md     standing context and rules for working on the repo
```
