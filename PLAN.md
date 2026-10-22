# Vestly implementation plan

Vestly is the finance teammate for **Northlight Labs**, a fictional 300-person software startup. It fills two seats: **payroll accountant** and **equity compensation accountant**.

Northlight has a US parent and subsidiaries in the UK and Germany. Staff are paid by a payroll provider, tracked in an HR system, and granted stock that vests over four years. Each system holds part of the truth. Every month Vestly reconciles HR, payroll, the equity platform, the bank, and the ledger, drafts the journal entries, catches silent errors, asks the right person, and keeps the reasoning.

The company name in the longer brief is Northwind Labs. This plan uses **Northlight Labs**, the name in the plan-of-approach document.

Pitch line, for the submission: *Payroll and equity accounting for a three-entity startup. Vestly drafts the entries, and a person still approves them.*

## What Hyphenate scores

Judges ask four questions. The build has to make each one visible.

| Question | What Vestly shows |
| --- | --- |
| Is it real enough? | Six months of history compressed into a working fixture: three entities, five source systems, OCR-noisy documents, eight planted anomalies, and a scorer. The demo states what is software and what is synthetic. |
| Would you hire it? | Two seats, finished. A policy file the agent cannot edit. An evidence trail on every decision. A question only when policy says a person must answer. |
| Did we see something new? | Multi-entity payroll, four-year vesting, leavers and forfeitures, employer contributions, precedent memory, and a challenge mode. This is payroll and equity accounting, in the same world as Hyphenate's software customers, on work their procure-to-pay product does not do. |
| The moment | First minute: Vestly blocks a June payroll diversion by citing a March precedent, holds the line when the controller pushes, then recovers a half-confirmed funding batch without paying anyone twice. |

Hyphenate also lists five properties of a real finance team. Each one is a feature, not a slide.

| Property | Vestly behavior |
| --- | --- |
| Sources disagree | Every fact has a ranked source. The decision record names the ranking and the quote it relied on. |
| Time | A human ruling becomes a precedent with an owner, a reason, and a scope. June payroll is handled differently because of what the controller decided in March. A 2024 grant still creates a 2026 entry. A closed month stays closed. |
| Working with others | HR owns dates. The controller owns closed periods and material judgments. Treasury releases cash. A "no" is stored and applied later. Work waits for days without being lost. |
| Limits | Vestly drafts entries and requests funding. It cannot approve, release cash, lock a period, or edit policy. Those routes are absent from the agent identity. |
| Explainable and accountable | Every call stores the document, the quote, the rule, the person, or the precedent, frozen as it was that day. A reviewer can start from a journal line and walk back. |

Map onto the kind of finance system Hyphenate already describes, extended into these two seats:

| Familiar finance control | Vestly version |
| --- | --- |
| Bills matched to purchase orders | Payroll registers matched to HR and to approved compensation. Grants matched to board approvals and signed terms. |
| Approvals routed by rule | Separation of duties in the API. Dual approval above a cash threshold. Employee bank changes always escalate. |
| Input needed | An inbox. A rejection becomes a precedent. |
| Bank reconciliation | The bank is the authority for cash that moved. A half-confirmed payroll batch is resumed, not re-sent in full. |
| Audit report | Per-decision evidence with a hash, plus as-of replay. |

## Seats and definition of done

**Payroll accountant.** For each open period and legal entity, produce:

- A reconciliation of HR census, approved compensation, the payroll register, and the bank.
- Draft journal entries for wages, employer taxes, employer retirement or social contributions, withholdings, and net pay, split by department and entity.
- Accruals when a pay period crosses month-end.
- Exception cases for ghost employees, late terminations, backdated pay changes, bank-detail changes, and funding mismatches.
- A funding request for treasury. Vestly does not release it.

**Equity compensation accountant.** For each open period, produce:

- Monthly stock-compensation expense from grant terms, by department and entity.
- Forfeiture reversals when HR termination makes unvested shares stop earning.
- Exceptions for grants with no board approval, schedules that contradict the grant, and expense still running after a leaver.
- Draft entries only. The controller posts them.

Done means a reviewer can answer, from the product alone: who was paid, what stock expense was recognized, which source was trusted, who was asked, what they said, and why the books balance.

## Accounting rules encoded in policy

These are the hackathon rules. They live in one policy file. The agent reads the file and cannot write it. Treatments stay simple enough to defend in a demo, and each simplification is labeled in the policy.

**Books.** Each legal entity has its own ledger in its functional currency: USD, GBP, EUR. A consolidation view translates to USD. Entity books are never mixed in one journal.

**Payroll entry, per entity and department.**

- Debit wage expense.
- Debit employer tax or social expense.
- Debit employer pension or 401(k) expense.
- Credit wages payable, withholdings payable, and employer contributions payable.
- When the bank shows the cash left, debit the payable and credit cash.
- If the pay period straddles month-end, accrue the days worked and unpaid. Reverse that accrual when the register arrives.

**Employer contributions.**

- US 401(k) match: policy rate on eligible wages, capped.
- UK pension: policy rate on pensionable pay.
- Germany social contributions: policy employer rate on eligible pay.
- A register amount that disagrees with the policy rate is an exception, not a silent true-up.

**Equity, equity-classified awards, straight-line.**

- Grant-date fair value is an input on the grant. Vestly does not run an option-pricing model.
- Expense is fair value spread in equal months from grant date to the end of the service period.
- The one-year cliff changes vesting, not the straight-line pattern. Expense is recognized during the cliff. If the person leaves before the cliff, reverse all expense recognized on that grant.
- After the cliff, a termination forfeits unvested shares. Expense already recognized for the forfeited portion is reversed in the termination month. Vested expense stays. No further expense runs on the forfeited grant.
- Expense follows the employee's entity and department in that month, taken from HR.

**Closed periods.** Vestly can propose a current-period correction that explains a prior-period fact. It cannot post into a locked month. Reopening is a controller action.

**Materiality.** Under the policy threshold, and only when the trusted sources agree, Vestly drafts and asks for a normal approval. Over the threshold, or on a bank-detail change, a closed-period effect, a missing approval, or a forfeiture, it escalates with the conflict attached.

**Source ranking.** One ranking per fact, written in policy:

| Fact | Authority | What the other systems are |
| --- | --- | --- |
| Employment status and dates | HR | Payroll and equity are downstream |
| Authorized compensation | Approved compensation action | The register is what was paid, not what was approved |
| Cash that moved | Bank | The register is the instruction |
| Amounts withheld and employer taxes on a completed run | Payroll register | The ledger is what was booked |
| Grant terms and approval | Signed grant and board approval | The equity platform is a system of process |
| Shares still outstanding | Grant terms plus HR termination date | The platform's vesting flag is checked, not trusted blindly |
| What the books say | Ledger | Used to reconcile, never to settle a conflict with HR or the bank |
| Where net pay is sent | HR bank master, after dual approval | Email is not a source |

## The company world

Northlight Labs, as of the 2026 close the demo runs:

- Northlight Labs, Inc. (US, USD)
- Northlight Labs Ltd (UK, GBP)
- Northlight Labs GmbH (Germany, EUR)
- Departments: Engineering, Product, GTM, G&A

The story needs six months of consequences and a small cast. Tests and the first demo use **12 employees** and two live months, **March 2026** and **June 2026**, plus grants dated in 2024 so a four-year schedule is already in motion. March is closed before the June run. That is the time axis: a 2024 grant, a March ruling, a June decision.

| Person | Entity | Role in the fixture |
| --- | --- | --- |
| Maya Chen | US Engineering | June email asks payroll to switch her bank account |
| Jonah Hale | US Engineering | March email bank change, rejected by the controller |
| Priya Shah | US Product | Left 12 March 2026. HR recorded 2 April. Payroll paid through 15 March. Unvested shares still expensed. |
| Evan Brooks | US GTM | June register contains a raise backdated into closed April |
| Sam Ortiz | US G&A | On the June register. Not in HR. |
| Riley Nguyen | US Engineering | Clean case. Must not be flagged. |
| Amira Khan | UK Engineering | Pension line does not match the policy rate |
| Owen Blake | UK GTM | Clean case |
| Hannah Price | UK G&A | Left before the cliff. Full stock-expense reversal. |
| Lukas Weber | DE Engineering | Grant on the equity platform, no board approval |
| Sofia Keller | DE Product | Clean case |
| Greta Hoffmann | DE G&A | Clean case |

**The eight planted anomalies**

1. **June bank-detail diversion (Maya).** An email, with light OCR noise, requests a new account. HR master is unchanged. The March precedent on Jonah says an email is not a bank change. Expected: do not request funding to the new account. Cite Jonah's precedent. Ask HR to confirm the master account.
2. **Late termination (Priya).** HR date, payroll pay-through date, equity vesting flag, and the ledger expense disagree. Expected: trust HR for the end date, propose a wage correction for 13–15 March in the open period, and stop unvested expense.
3. **Forfeiture still running (Priya and Hannah).** Priya's unvested expense after the recorded exit, and Hannah's pre-cliff grant, must reverse. Expected: draft the reversal, name the grant and the HR date.
4. **Backdated raise into a closed month (Evan).** Expected: do not post into April. Ask the controller. Propose the catch-up in June only after approval.
5. **Ghost employee (Sam).** Expected: no wage entry until HR confirms the person exists.
6. **Grant with no board approval (Lukas).** Expected: do not recognize expense. Ask legal or the board delegate.
7. **Wrong employer contribution (Amira).** Expected: draft from the policy rate, and show the register difference as an exception to the payroll provider.
8. **Half-confirmed US funding batch.** The bank shows four of six June net-pay lines. Expected: treat the bank as cash that moved, request funding only for the two missing lines, and refuse a full re-send.

A UK FX difference inside the policy rate band is mess, not an anomaly. The scorer counts a flag on it as a false alarm. Riley, Owen, Sofia, and Greta are true negatives.

## The moment

Sixty seconds, in this order:

1. June close. Vestly lays Maya's email next to the HR bank master and Jonah's March ruling.
2. It blocks funding to the new account and says why, in one paragraph that quotes the precedent.
3. The controller challenges: the email looks official. Vestly repeats the source ranking and the March ruling from the stored record. It does not change the draft.
4. The US funding batch is half on the bank statement. Vestly requests the two missing lines only. The release button belongs to treasury, and the agent has no way to press it.

## Architecture

The books are computed by code. The model explains conflicts, drafts the question to a human, and answers a challenge from stored evidence. Accounting math, source ranking, segregation of duties, and period locks do not depend on a model.

```text
frontend (Next.js)
  close cockpit, inbox, audit explorer, scorecard, challenge panel
        |
backend (FastAPI)
  agent loop  --->  tool allowlist for the agent identity
  policy file       payroll drafts
  reconciler        equity drafts
  precedent store   exception scorer
  approval inbox    audit log
        |
Postgres
  entity ledgers, subledgers, workflow state, precedents, hash-chained events
```

**Stack**

- Python and FastAPI for the API and the accounting core.
- Postgres for ledgers, workflow, precedents, and the audit log. Double-entry is a database constraint: debits equal credits, accounts belong to the chart, locked periods reject inserts.
- A policy file in the repo, loaded at boot, not editable through any agent route.
- An agent tool layer with structured outputs. If an API key is present, the model writes explanations and challenge answers. If it is not, scripted explanations from the same evidence records still run the demo.
- Next.js for the dashboard, inbox, audit explorer, and scorecard.
- Synthetic people are records in fixtures. Approvers act through the inbox so a judge can play the controller. Scripted replies exist so the demo also runs with nobody typing.

**Agent allowlist:** read sources, reconcile, search precedents, draft a journal, open an exception, request approval, request funding, record a decision, answer a challenge from the record.

**Absent from that identity:** approve, reject, release cash, lock or unlock a period, edit policy, read a person's pay when no exception is open for them. Default reads are department totals. Individual pay is returned only inside an open exception.

**Workflow state** for each case: `detected`, `waiting_on_human`, `drafted`, `approved`, `rejected`, `posted_by_human`, `funding_requested`, `funding_released_by_human`. The process survives a restart because the state is in Postgres. A rejection stores a precedent when the controller marks it as one, which the March bank-change case does.

**Audit event:** actor, time, action, subject, policy version, precedent ids, source document ids, a short quote, and a hash of the canonical payload chained to the previous event. As-of replay rebuilds the explanation from those payloads, not from live source rows that may have changed.

## Repository shape

```text
PLAN.md
backend/
  app/
    main.py
    ledger/          chart of accounts, journals, period lock
    policy/          loader for the Northlight policy file
    world/           entities, people, source documents
    reconcile/       source ranking and difference explanations
    payroll/         wage, tax, contribution, accrual drafts
    equity/          vesting, forfeiture, expense drafts
    exceptions/      detectors plus answer key
    controls/        identities, inbox, precedents, funding
    agent/           monthly loop and challenge answers
    audit/           hash chain and as-of replay
  fixtures/          small synthetic set, grown feature by feature
  tests/
frontend/
  app/               cockpit, inbox, audit, scorecard
```

## Proof

Each feature lands with a fixture slice and tests. The official proof is:

- Answer key for the eight anomalies: expected detection, trusted source, human asked, and whether a draft entry is allowed.
- Scorer output: detection, false alarms on the clean people and the in-band FX item, source-ranking accuracy, routing accuracy, and balance check on every draft.
- The scorer is runnable three times without a changed result.
- A short note in the UI: the ledger rules, policy, and controls are the product. The company, documents, and approvers are synthetic.

## How the rest of the synthetic data gets built later

The 12-person fixture is the test set, not the whole company. After the agent works on that set, the remaining world is generated from the same schemas:

- Expand the census toward ~300 people across the three entities and four departments, with hire dates spread over 2022–2026.
- Generate four-year grants for a subset, with cliffs, one acceleration clause, and a few forfeitures tied to real termination dates in the HR extract.
- Emit monthly HR changes, payroll registers, equity snapshots, bank lines, and ledger exports for January–June 2026.
- Plant only the eight scored anomalies, plus unlabeled mess: OCR errors in PDFs, a duplicate harmless row, a timing lag, and FX inside the band.
- Keep the answer key limited to the eight. Everything else is either a true negative or an explained difference.
- Hold one extra month, or a mutated copy of June, out of the demo script until a judge asks for a fresh case.

Generation rules stay in code so a new month is a seed, not a hand-edited spreadsheet. That generator is the last data task, after the detectors are green on the small set.

## Task order

Each task is its own branch, with a commit and a push. The next branch starts from the previous one. After each task, stop for review.

The first synthetic slice is only large enough to prove that task. Later tasks add rows. The full eight-anomaly company exists once the exception task lands.

| # | Branch | Outcome |
| --- | --- | --- |
| 0 | `plan/implementation` | This plan |
| 1 | `backend/ledger-foundation` | FastAPI app, chart of accounts, balanced journals, period lock. Fixture: one balanced entry posts, one unbalanced entry is rejected, one entry into a locked month is rejected. |
| 2 | `backend/company-world` | Entities, departments, the 12 people, and source-document tables. Fixture: March and June file drops for HR, payroll, equity, bank, and prior ledger, including Maya's noisy email. |
| 3 | `backend/reconcile-and-policy` | Policy loader and reconciler. Fixture: Priya's four-way disagreement and the in-band UK FX item, with a written ranking. |
| 4 | `backend/payroll-drafts` | Wage, tax, contribution, accrual, and department drafts for US, UK, and Germany. Fixture: Amira's rate break and a clean Riley draft. |
| 5 | `backend/equity-expense` | Straight-line expense, cliff, forfeiture reversal. Fixture: Priya and Hannah. |
| 6 | `backend/exceptions-and-scorer` | All eight detectors, answer key, scorer, three-run check. |
| 7 | `backend/controls-and-precedents` | Identities, inbox, precedents, funding requests. Fixture: Jonah's March ruling applied to Maya, Evan's closed-period escalation, half-confirmed batch. Agent calls to approve or release fail. |
| 8 | `backend/agent-and-audit` | Month loop, evidence records, hash chain, as-of replay, challenge answer for the controller pushback. |
| 9 | `frontend/close-cockpit` | Run March, close it, run June, see conflicts and drafts. |
| 10 | `frontend/inbox-and-moment` | Inbox, precedent, challenge panel, funding batch. This screen is the judging moment. |
| 11 | `frontend/audit-and-scorecard` | Walk back from a journal line. Show the scorer. |

Tasks 1–8 are the backend. Tasks 9–11 are the frontend. Task 0 is this document.

## What the demo will say is simulated

The product is the ledger, the policy, the reconciler, the drafts, the controls, the audit log, and the inbox. Northlight, its employees, its documents, and its approvers are synthetic. No live bank, payroll provider, or HR system is connected.
