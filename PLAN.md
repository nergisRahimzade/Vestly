# Vestly build plan

Vestly is the finance teammate for **Northlight Labs**, a fictional 300-person software startup with a US parent and subsidiaries in the UK and Germany. It fills two seats: **payroll accountant** and **equity compensation accountant**. Every month it reconciles HR, payroll, the equity platform, the bank, and the ledger, drafts the journal entries, catches silent errors, asks the right person, waits for the answer, and keeps the reasoning.

The longer brief calls the company Northwind Labs. This plan and the code use **Northlight Labs**.

**One line on the seats (for the submission):** *Vestly fills the payroll accountant and equity compensation accountant seats for a three-entity startup. It drafts every entry, and a person still approves it.*

This document has four parts:

1. What Hyphenate asks for, and where each ask is answered in the build.
2. The rules Vestly works by: accounting, source ranking, controls.
3. The company world and the scored anomalies.
4. The task list, split by layer: **Data**, **Backend**, **AI/ML**, **Frontend**.

---

## 1. What Hyphenate asks for

### The four judging questions

| Question | What a judge must be able to see | Tasks |
| --- | --- | --- |
| **Is it real enough?** Realistic data, a messy problem, proof it works. | Three entity books in three currencies. Five source systems that disagree. OCR-noisy emails. Eleven planted anomalies plus unlabeled mess. An answer key and a scorer that report detection, false alarms, source calls, routing, and balance. A held-out scenario a judge can trigger. | 3, 7, 19, 20 |
| **Would you hire it?** For which seats, with limited access, a clean record, and knowing when to ask. | Two seats with a defined output per month. Identities issued by the server. The agent cannot approve, post, release cash, lock or reopen a period, or edit policy. It sees department totals by default. Every action is in a hash-chained log. Materiality and policy decide when it asks. | 1, 2, 8, 9, 10, 13 |
| **Did we see something new?** | Payroll and four-year equity in one system, so a 2024 grant still makes a 2026 entry. A ranking per fact. Precedents: a March ruling changes June behavior. Autonomy: a file drop starts the close without anyone pressing a button. | 5, 8, 13 |
| **The moment.** Catches what others missed, recovers, or defends a decision. | The first sixty seconds of the demo (section 1.3). | 16 |

### The five hard parts

| Hard part | What Vestly does | Tasks |
| --- | --- | --- |
| **Sources disagree** | Every fact has a ranked list of sources in the policy file. A finding quotes each source's claim, names the trusted one, and says why. | done, 6 |
| **Time** | A ruling becomes a precedent with an owner, a scope, and a reason. Old grants keep producing entries. A locked month stays locked; a late fact becomes a current-period correction or a controller-approved reopen. As-of replay answers "what did we know then". | 5, 8, 10 |
| **Working with others** | A routing map says who owns each question. Cases wait for days on a simulated clock, send reminders, handle partial answers, and treat a "no" as an input that changes the plan. | 8, 9, 12 |
| **Limits** | Least-privilege tool allowlist. Separation of duties is checked by the database: the preparer cannot approve, the person who changed pay cannot approve that payroll run, and dual approval is required above the cash threshold. | 1, 2, 8 |
| **Explainable and accountable** | Every decision stores the documents, quotes, rule, policy hash, precedent, and person. A reviewer starts from any journal line and walks back to the source rows. | 10, 14, 17 |

### 1.3 The moment

The demo video opens with this sequence. Total about sixty seconds.

1. **Catching what others missed.** The June close starts by itself when the June file drop lands. Vestly lays Maya Chen's bank-change email next to the HR bank master and the controller's March ruling on Jonah Hale.
2. It holds Maya's funding line and says why in one paragraph that quotes the March precedent.
3. **Defending a decision.** The controller pushes back: "the email looks official, just pay it." Vestly answers from the stored record: the source ranking, the precedent, the policy hash. The draft does not change.
4. **Recovering.** The bank shows the US June batch only partly cleared. Vestly requests funding for the two legitimate missing lines only, and refuses a full re-send. The release button belongs to treasury, and the agent identity has no route to it.

Second beats, shown in the live walkthrough:

- **Forfeitures.** "Why did June stock expense drop?" Vestly traces it to Priya's and Hannah's forfeitures, each linked to an HR termination row and a grant.
- **Reopen.** HR answers the Priya question with a corrected date that falls in locked March. Vestly asks the controller to reopen through the approval path instead of rewriting history.
- **Limits.** Someone asks for everyone's salary. Vestly declines and offers department totals.

### What to submit, and where it comes from

| Submission item | Where it comes from | Task |
| --- | --- | --- |
| Demo video that opens with the moment | Recorded from the moment screen | 16, 21 |
| Live demo, with a scenario chosen by a judge | Scenario injector plus a reset script | 19, 21 |
| One line on the seats | This document, top | 21 |
| Proof: what's real, what's simulated, how we know | Scorecard page, answer key, a "what is simulated" panel, the README | 7, 17, 21 |

---

## 2. The rules Vestly works by

These live in `backend/app/policy/northlight.yaml`. The agent reads the file and cannot write it. Every finding and every decision stores the policy version and the hash of the file content. Simplifications are labeled in the file.

### Books

- Each legal entity has its own ledger in its functional currency: US in USD, UK in GBP, DE in EUR. One journal never mixes entities.
- A consolidation view translates to USD at the reference rate. A management-pack rate inside the policy band is an explained difference, not an exception.

### Chart of accounts

Existing accounts stay. These are added when the task that needs them lands:

| Code | Name | Added in |
| --- | --- | --- |
| 1200 | Employee overpayment receivable | Task 4 |
| 1210 | Payroll suspense (cash out, person not confirmed) | Task 4 |
| 2150 | Accrued wages | Task 4 |
| 2160 | Accrued employer taxes and contributions | Task 4 |

### Payroll entry, per entity and department

- Debit wage expense (5000), employer tax expense (5100), and employer pension or social expense (5110).
- Credit net pay to wages payable (2100), withholdings payable (2200), employer taxes payable (2210), and contributions payable (2220).
- When the bank shows the cash left, debit wages payable and credit cash.
- When a pay period crosses month-end, accrue the worked but unpaid days at the HR rate (2150, 2160). Reverse the accrual on the first day of the next period.
- Pay for days after an HR termination date is an overpayment. It goes to 1200 and is routed to HR and payroll for recovery. It is not a quiet wage reduction.
- Cash that already left for a person HR has not confirmed goes to suspense (1210), not wages.

### Employer contributions

- US 401(k) match: policy rate on gross wages.
- UK pension: policy rate on pensionable pay.
- Germany social contributions: policy employer rate on gross pay.
- A register amount that differs from the policy rate is an exception sent to the payroll provider. Vestly drafts from the policy rate and shows the difference.

### Equity: equity-classified awards, straight-line

- Grant-date fair value is an input on the signed grant. Vestly does not price options.
- Expense is fair value spread in equal months over the 48-month service period. This is a policy election allowed for service-only awards under ASC 718. Cumulative expense is never below the vested portion.
- Forfeitures are recognized when they occur, which is also a policy election. Before the cliff, a termination reverses all expense on the grant. After the cliff, expense recognized for the unvested portion reverses in the termination month. No expense runs after the HR termination date.
- Expense follows the employee's entity and department in HR for that month, so a transfer moves the expense.
- A grant without board approval recognizes no expense until approval arrives.
- Each grant has its own expense schedule (the equity subledger). The ledger is reconciled against the subledger, not against department totals.

### Closed periods

- Vestly can propose a current-period correction that explains a prior-period fact.
- It cannot post into a locked month. Reopening is a controller action, recorded with a reason, and relocking is required after the correction posts.

### Materiality

- One threshold: **25.00 USD**, set in the policy file. UK and DE differences are translated to USD at the period's reference rate before comparing. With no reference rate, Vestly escalates rather than guess.
- At or below the threshold, and only when the trusted sources agree, Vestly drafts and asks for a normal approval. Above it, Vestly escalates with the conflict attached.
- `app/policy/materiality.py` applies the rule. `tests/test_materiality.py` tests the rule itself; from task 3, the answer-key test checks that the data exercises both sides of the line, so the threshold can be re-checked once the full data exists.
- It always escalates, at any amount, on: a bank-detail change, a closed-period effect, a missing grant approval, a forfeiture, an unconfirmed person, and a funding mismatch.

### Source ranking

One ranking per fact. Already in the policy file.

| Fact | Authority | The other systems |
| --- | --- | --- |
| Employment status and dates | HR | Payroll and equity are downstream |
| Authorized compensation | Approved compensation action | The register is what was paid, not what was approved |
| Cash that moved | Bank | The register is the instruction |
| Withholdings and employer taxes on a completed run | Payroll register | The ledger is what was booked |
| Grant terms and approval | Signed grant and board approval | The equity platform is a system of process |
| Shares still outstanding | Grant terms plus HR termination date | The platform's vesting flag is checked, not trusted |
| What the books say | Ledger | Never settles a conflict with HR or the bank |
| Where net pay is sent | HR bank master, after dual approval | Email is not a source |
| GBP to USD translation | Reference rate | The management pack rate is explained inside the band |

### Controls

**Identities.** The server issues them. A caller presents a token. The role comes from the server's identity table, never from a header the caller writes.

| Identity | Role | Can | Cannot |
| --- | --- | --- | --- |
| `vestly` | agent | Read sources (scoped), reconcile, search precedents, draft journals, open cases, ask, request approval, request funding, record decisions, answer from the record | Approve, reject, post, release cash, lock or reopen, edit policy, read one person's pay without an open case on that person |
| `dana.ruiz` | controller | Approve or reject drafts, post, lock, reopen, mark a ruling as a precedent | Prepare an entry they approve |
| `lee.park` | hr_ops | Answer date and person questions, update the bank master | Approve a payroll run that contains a pay change they entered |
| `sam.kerr` | hr_comp | Enter compensation changes | Approve the run that pays them |
| `alex.moreau` | treasury | Release funding | Release above threshold alone |
| `jordan.hayes` | treasury | Second release approval above threshold | Request and release the same batch |
| `morgan.li` | legal | Answer grant-approval questions, record a board approval | Post |
| `payroll.provider` | payroll_provider | Answer tax and contribution questions | Read the ledger |
| `head.engineering`, `head.product`, `head.gtm`, `head.ga` | dept_head | Answer cost-center questions for their own department | See other departments' pay |
| `riley.auditor` | auditor | Read the audit log and replay, read everything as of a date | Change anything |
| `stranger` | none | Nothing | Everything |

**Separation of duties, enforced in the database:**

- A journal's `prepared_by` cannot equal its `approved_by`.
- The identity that entered a pay change cannot approve the run that pays it.
- Funding above the cash threshold needs two different treasury identities.
- The requester of a funding batch cannot release it.

**Data access.** The agent's default views are totals per entity and department. Individual pay is returned only for people with an open case, and every such read is logged.

### Case workflow

Every exception is a case with one state at a time:

`detected` → `waiting_on_human` → (`answered` | `partially_answered` | `rejected` | `expired`) → `drafted` → `approved` → `posted_by_human`

Funding: `funding_requested` → `funding_released_by_human` (or `funding_held`).

A case stores who owns the question, when it is due, how many reminders went out, and the answer. State lives in Postgres, so a restart loses nothing. A rejection the controller marks as a precedent becomes one.

### Audit event

Each event stores: actor, time (real and simulated), action, subject, policy version and hash, precedent ids, source document ids and content hashes, a short quote, the payload, and a SHA-256 hash of the canonical payload chained to the previous event's hash. As-of replay rebuilds an explanation from stored payloads, not from live rows that may have changed.

---

## 3. The company world

### Entities and departments

- Northlight Labs, Inc. (US, USD)
- Northlight Labs Ltd (UK, GBP)
- Northlight Labs GmbH (Germany, EUR)
- Departments: Engineering, Product, GTM, G&A

### Months

The test world runs **March 2026** (closed before June runs) and **June 2026** (the live close), with grants from 2024 and 2025 already in motion. **July 2026** is the held-out month, generated but not shown until a judge asks.

UK pay runs 26th to 25th, the common UK cycle. That makes the UK month-end accrual real. US and DE pay calendar months.

### The test cast: employees

The 12 people below already exist in the fixtures. Task 3 adds three more so the new anomalies don't pile onto one person.

| Person | Entity / dept | Role in the fixture |
| --- | --- | --- |
| Maya Chen | US Engineering | June email asks payroll to switch her bank account. The provider acted on it: her June funding line points at the emailed account. |
| Jonah Hale | US Engineering | March email bank change, rejected by the controller and marked as a precedent. His platform vesting end date is a typo. |
| Priya Shah | US Product | Left 12 March. HR recorded 2 April. Payroll paid through 15 March. Unvested shares still expensed. |
| Evan Brooks | US GTM | June register contains a raise backdated into locked April. The raise was entered by `sam.kerr`. |
| Sam Ortiz | US G&A | On the June register and paid. Not in HR. |
| Riley Nguyen | US Engineering | Clean. Must not be flagged. |
| Amira Khan | UK Engineering | Pension at 3% on the register, policy says 5%. |
| Owen Blake | UK GTM | Clean. |
| Hannah Price | UK G&A | Left before the cliff. Full expense reversal. |
| Lukas Weber | DE Engineering | Grant on the platform with no board approval. |
| Sofia Keller | DE Product | Clean. |
| Greta Hoffmann | DE G&A | Clean. |
| *Nadia Rossi* (new) | US Product | Her grant appears twice on the equity platform. |
| *Tomas Fischer* (new) | DE → UK Engineering | HR transfers him to the UK on 1 June. June payroll and stock expense still sit in DE. |
| *Ines Duarte* (new) | UK Product | Clean. Starts 2 June, mid-cycle, so the first UK pay is partial. Must not be flagged. |

### The eleven scored anomalies

| # | Anomaly | Person | Trusted source | Ask | Draft allowed? |
| --- | --- | --- | --- | --- | --- |
| 1 | Bank-detail diversion by email | Maya | HR bank master | HR | No funding to the emailed account |
| 2 | Late termination | Priya | HR | HR | Overpayment to 1200 in June, after HR confirms |
| 3 | Forfeiture still being expensed | Priya, Hannah | HR + signed grant | Controller | Reversal draft |
| 4 | Backdated raise into a locked month | Evan | Approved comp action | Controller | Catch-up in June only after approval |
| 5 | Ghost employee | Sam | HR | HR | No wage entry; cash already gone goes to suspense |
| 6 | Grant with no board approval | Lukas | Board approval | Legal | No expense |
| 7 | Wrong employer contribution | Amira | Policy rate | Payroll provider | Draft at policy rate, difference shown |
| 8 | Half-cleared funding batch | US June batch | Bank | Treasury | Request missing legitimate lines only |
| 9 | Vesting schedule typo | Jonah | Signed grant | Legal | Expense from the signed schedule |
| 10 | Duplicate grant | Nadia | Signed grant | Legal | Expense once |
| 11 | Cross-entity transfer not reflected | Tomas | HR | Dept head (Engineering) and controller | Expense and wages follow HR's entity |

**True negatives:** Riley, Owen, Sofia, Greta, Ines, and the in-band GBPUSD difference. Flagging any of them counts as a false alarm.

**Unlabeled mess,** which must be explained rather than flagged: OCR typos in emails, one duplicated harmless HR row, a bank line posted one day after month-end, and the FX difference inside the band.

### The answer key

`backend/fixtures/answer_key.json` holds, for each anomaly: the id, the person or batch, the expected detection, the trusted source, the identity that should be asked, whether a draft is allowed, and the expected amount where there is one. The scorer reads only this file and the agent's stored output.

---

## 4. Tasks

### How each task runs

- One task, one branch. The new branch starts from the previous one:

  ```text
  git branch <new_branch>
  git checkout <new_branch>
  git pull origin <previous_branch>
  ...work...
  git add .
  git commit -m "<message>"
  git push origin <new_branch>
  ```

- Each task lands with its fixture slice and tests. A task is done when the full test suite passes with `backend/.venv`, and with Postgres when Docker is running.
- After each task: a summary of what changed, then stop until told to move on.
- Layer tags: **[Data]**, **[Backend]**, **[AI/ML]**, **[Frontend]**. Some tasks cross layers; the tag names the main one.

### Already done

| # | Branch | Outcome |
| --- | --- | --- |
| — | `plan/implementation` | First plan |
| — | `backend/ledger-foundation` | FastAPI app, chart of accounts, balanced journals, period lock as database triggers |
| — | `backend/company-world` | Entities, the 12 people, March and June file drops |
| — | `backend/reconcile-and-policy` | Policy file, ranked sources, Priya's four-way conflict, in-band FX |
| — | `plan/full-build` | This plan |

### Phase A: fix the foundation before building on it

**Task 1 · [Backend] `fix/identity-and-access`**

Gaps: the role comes from a header anyone can set, and the agent can read every salary and bank account.

- Add an `identities` table and token auth. Seed the identities in section 2. Remove `X-Role` and `X-Actor`.
- Move authorization to a single `require(identity, permission)` check. The permission map per role lives in code, so no route can grant a permission.
- Give the agent aggregate views: `/world/payroll/totals`, `/world/hr/headcount`, and totals for equity, funding, and bank. A group covering fewer people than the policy's `min_group_size` shows headcount only. Return individual rows only for people with an open case. Until cases exist (task 8), nobody has one, so the agent gets totals only.
- Log every individual-pay read to a temporary `access_log` table. The audit chain absorbs it in task 10.
- Pin exact versions in `requirements.txt`. Add a `Makefile` or `scripts/test.ps1` that always uses `.venv`.
- Tests: a forged role is rejected; `vestly` gets 403 on post, lock, and individual pay; `stranger` gets 401 or 403 everywhere; the controller still posts.

**Task 2 · [Backend + Data] `fix/history-ledger-states-and-generator`**

Gaps: reconciliation re-runs erase history, the ledger has no draft state, materiality is one number in USD, and the generator only knows March and June.

- Reconciliation runs become append-only: a `reconciliation_runs` table (run id, actor, time, input document hashes, policy hash). Findings belong to a run. Reads return the latest run by default, or any run by id.
- Journal states `draft` → `approved` → `posted`, with `prepared_by`, `approved_by`, `posted_by`. A database check stops `prepared_by = approved_by`. The agent can create drafts only.
- Materiality stays at 25.00 USD. The rule, the currency translation, and the always-escalate list are written in the policy file and applied by one function, with tests at the boundary.
- Generator: `build_drop(period)` works for any month. Month names, month-end dates, and pay calendars are computed, not hard-coded. Each anomaly is a named "plant" function applied to a clean month, so a held-out month is `build_drop("2026-07", plants=[...])`.
- Tests: two reconciliation runs both survive; an agent draft can't be approved by the agent; a generated April has valid month-ends; the committed March and June fixtures still match the generator byte for byte.

### Phase B: the data the features need

**Task 3 · [Data] `data/test-world-and-answer-key`**

- Add Nadia, Tomas, and Ines. Plant anomalies 9–11. Point Maya's June funding line at the emailed account and leave it uncleared. Set up the UK 26th-to-25th cycle.
- Add the unlabeled mess: a duplicated harmless HR row, a bank line posted on 1 July, and the OCR typos already present.
- Write `fixtures/answer_key.json` for all eleven anomalies and the true negatives.
- Add `fixtures/signed_grants/` (one small text document per grant, with fair value, schedule, and approval line) and `fixtures/comp_actions/` (approved compensation changes, with who entered and who approved).
- Tests: the answer key validates against a schema; every person in the key exists in the fixtures; every fixture ledger entry balances.

### Phase C: the accounting core

**Task 4 · [Backend] `backend/payroll-drafts`**

- Draft payroll journals per entity and department from the register, using policy rates for contributions.
- UK month-end accrual for 26–30 June, and its reversal in July.
- Overpayment receivable for Priya's 13–15 March pay, drafted in June.
- Suspense posting for Sam's cleared payment.
- Hold rules: no wage line for an unconfirmed person, no draft into a locked month.
- Accounts 1200, 1210, 2150, 2160.
- Test cast: Riley (clean draft), Amira (rate break), Ines (partial first pay), Sam (suspense), Priya (overpayment).
- Tests: every draft balances; Riley's draft matches a hand calculation to the cent; Amira's draft uses 5% and lists the 2% difference.

**Task 5 · [Backend] `backend/equity-subledger`**

- A grant schedule per grant: monthly expense, cumulative expense, vested shares, status.
- The cliff, forfeiture before and after the cliff, no expense without board approval, and expense following the HR entity and department each month.
- Monthly equity drafts by entity and department, built from the subledger.
- Reconcile the subledger to the ledger per grant. This replaces the "only grant in the department" shortcut in the reconciler.
- Test cast: Priya (post-cliff forfeiture), Hannah (pre-cliff full reversal), Lukas (no approval), Tomas (transfer), Riley (clean), Jonah (signed schedule versus the platform typo).
- Tests: cumulative expense never falls below the vested portion; Hannah's reversal equals all expense recognized; a 2024 grant produces a 2026 line.

**Task 6 · [Backend] `backend/exception-detectors`**

- One detector per anomaly type, each returning a finding with ranked claims, the trusted value, the owner to ask, and whether a draft is allowed.
- Detection runs in both directions: from HR to payroll (late termination) and from payroll to HR (ghost employee), so a person HR doesn't know about is still seen.
- Detectors for the bank-change email (parse the account number out of noisy text), backdated raise against comp actions, funding against bank, contribution rate, vesting typo, duplicate grant, transfer.
- Test cast: every anomaly person plus every true negative.
- Tests: each detector fires on its case and stays silent on all five clean people and the FX item.

**Task 7 · [Data] `data/scorer`**

- `python -m app.eval.score --period 2026-06` compares stored findings and cases with the answer key.
- Reports detection rate, false alarms, correct trusted source, correct routing, draft-allowed correctness, and whether every draft balances.
- Runs three times and fails if any result changes.
- Tests: a perfect stub output scores 100%; a stub with one false alarm and one miss scores what it should.

### Phase D: working with people, over time

**Task 8 · [Backend] `backend/cases-inbox-precedents`**

- Cases with the state machine in section 2, a routing map from anomaly type to owner identity, and an inbox per identity.
- Answers: approve, reject, partial answer, corrected value. Each one moves the case and is stored.
- Precedents: the controller marks a ruling as a precedent with a scope (fact, source type, entity). Matching is structured and deterministic. A precedent is cited only if its scope matches.
- Funding requests: Vestly requests per payment line. Treasury releases. Dual approval above threshold. Requester ≠ releaser.
- Reopen path: Vestly requests a reopen with the reason; the controller reopens; the correction posts; the controller relocks.
- Seed Jonah's March rejection as precedent P-2026-03-001.
- Test cast: all identities from section 2.
- Tests: Maya's case cites P-2026-03-001; the agent calling approve or release gets 403; `sam.kerr` cannot approve the June US run; one treasury identity can't release above threshold alone; Evan's catch-up drafts only after controller approval.

**Task 9 · [Backend] `backend/durable-workflow-and-clock`**

- A simulated clock (`/clock/advance`, controller-only in the demo) so "three days later" takes one click.
- A Postgres job table: due time, attempts, case id. A worker picks up due jobs: reminders, escalation after the second reminder, case expiry.
- Restart safety: kill the process mid-wait, restart, and the case continues from its stored state.
- Tests: a reminder fires after the policy wait; a restart in `waiting_on_human` resumes; a rejection moves the case to `rejected` and the plan changes (no draft).

**Task 10 · [Backend] `backend/audit-chain-and-replay`**

- An append-only `audit_events` table with the hash chain. The database rejects updates and deletes.
- Every write path emits an event: findings, drafts, cases, answers, precedents, funding, reopen, individual-pay reads.
- `GET /audit/verify` recomputes the chain and reports the first broken link.
- `GET /audit/replay?as_of=...` rebuilds what Vestly knew at that time.
- `GET /audit/trace/journal-line/{id}` walks back to source rows, documents, rule, person, and precedent.
- Tests: tampering with one row breaks verification at that row; replay before HR's 2 April record shows Priya as active.

### Phase E: the agent

**Task 11 · [AI/ML] `ai/explanations-and-questions`**

- A Claude client behind one interface, with the model id from the environment. Without an API key, scripted templates fill the same structure, so the demo and tests run offline.
- The model receives only the evidence record for one case and returns structured output: an explanation, the question to the owner, and the evidence ids cited.
- A faithfulness check: every number, date, account, and person in the text must appear in the evidence record. If not, Vestly falls back to the template and logs why.
- The model never computes amounts, picks the trusted source, or chooses the owner. Code does that.
- Tests: with a stub model that invents a number, the check rejects the output; the scripted mode is deterministic.

**Task 12 · [AI/ML] `ai/simulated-approvers`**

- Personas for HR, controller, treasury, legal, payroll provider, and department heads. Each has a delay, a refusal rate, and a partial-answer rate, set in a config file.
- Two modes: scripted (fixed replies, for tests and the recorded demo) and model-played (for live runs). A judge can also answer by hand from the inbox.
- Scripted replies include: HR answering Priya's question with a corrected date in locked March (the reopen beat), the controller pushing back on Maya, and legal saying "no board approval yet" for Lukas.
- Tests: the scripted run is deterministic; a refusal leads to the right state; a partial answer triggers a follow-up question.

**Task 13 · [Backend + AI/ML] `agent/month-close-loop`**

- The month-close loop: a file drop arrives → load → reconcile → detect → open cases → draft → ask → wait → resume on answers → request funding → hand drafts to the controller.
- It starts by itself when a drop lands in the inbox folder or through `/drops` (this is the autonomy Hyphenate asks for), and on the clock tick.
- The agent calls only the allowlisted tools in section 2, with its own token. Calls outside the allowlist fail and are logged.
- Tests: from an empty database, March runs and the controller closes it, then June runs, then the scorer reports at least the target on the answer key; killing the loop mid-June and restarting finishes without duplicate cases or duplicate funding requests.

**Task 14 · [AI/ML] `ai/challenge-and-reviewer-qa`**

- Answers the controller's pushback and reviewer questions ("why did this number change", "who approved this", "what did we know on 1 April") using only audit events and precedents, each answer citing event ids.
- Data-limit refusals: requests for individual pay outside an open case are declined with an offer of department totals. Decided by code, phrased by the model.
- Tests: the Maya challenge answer cites P-2026-03-001 and the policy hash, and the draft doesn't change; "show me everyone's salary" returns a refusal and the aggregate offer; the June stock-expense question traces to Priya's and Hannah's events.

### Phase F: the interface

**Task 15 · [Frontend] `frontend/scaffold-and-cockpit`**

- Next.js app with a login picker for the identities in section 2. The token sets the role; the UI never sends a role.
- Close cockpit: entity and period tiles, run status, findings with ranked claims side by side, and drafts with balance totals.
- Tests: Playwright smoke test that runs March, closes it, and runs June.

**Task 16 · [Frontend] `frontend/inbox-and-moment`**

- An inbox per identity: open questions, due dates, reminders, approve, reject, partial answer.
- The moment screen: Maya's email, the HR bank master, and the March precedent side by side; the challenge panel; the funding batch with cleared and missing lines; a treasury-only release button.
- Tests: Playwright walk through the sixty-second moment in scripted mode.

**Task 17 · [Frontend] `frontend/audit-and-scorecard`**

- Audit explorer: start from a journal line and walk back. Chain verification status. As-of replay date picker.
- Scorecard: the scorer's output, with each anomaly linked to its case.
- A "what is real, what is simulated" panel.
- Tests: Playwright trace from a June equity line back to Priya's HR row.

### Phase G: proof and delivery

**Task 18 · [Data] `data/held-out-month`**

- Generate July 2026 with a different mix of plants (for example a new late termination, a second bank-change email, an unapproved grant in the UK). Keep its answer key separate.
- Tests: the agent runs July without changes to code, and the scorer reports on it.

**Task 19 · [Backend + Frontend] `demo/judge-scenarios`**

- A scenario injector: pick an anomaly type, a person, and a month, and it rewrites that month's drop through the generator's plant functions. Available from the UI.
- A reset script that returns the world to a known state in seconds.
- Tests: injecting a ghost employee into July makes the ghost detector fire, the scorer count it, and the case route to HR.

**Task 20 · [Data] `data/scale-to-company`**

- Extend the generator to about 300 people across three entities and four departments, hire dates from 2022 to 2026, grants for a subset with cliffs, and January to June history.
- Keep the scored plants limited to the answer key; everything else is a true negative or explained mess.
- Tests: performance (the June close finishes within the demo budget); the false-alarm rate on the 300-person world stays at the target.

This task builds the generator scaffolding and one scaled run. The rest of the scaled data is yours to create using the guide from task 22.

**Task 21 · [All] `demo/hardening-and-submission`**

- Docker Compose for Postgres, backend, and frontend. One command to reset and start.
- README: one line on the seats, how to run, what's real, what's simulated, and the scorecard numbers.
- Demo script and video shot list, opening with the moment.

**Task 22 · [Data] `docs/synthetic-data-guide`**

- `docs/SYNTHETIC_DATA.md`: how the generator is organized, how to add a person, a grant, a month, a plant, an answer-key entry, and an unlabeled mess item; how to check your data (tests, scorer, balance); and the rules that keep it realistic.
- A walkthrough in chat after this task.

### If time runs short

The moment depends on tasks 1–3, 4, 6, 8, 10, 13, and 16. Cut from the end: task 20 first, then 18, then 17's replay picker, then 12's model-played mode (scripted approvers still run the demo).

---

## What the demo says is simulated

**Real:** the ledger and its database constraints, the policy and source ranking, the reconciler and detectors, the drafts, the controls and separation of duties, the case workflow, the audit chain, and the scorer.

**Simulated:** Northlight Labs, its employees, its documents, its bank and payroll provider, the approvers, and the clock. No live bank, payroll provider, or HR system is connected.
