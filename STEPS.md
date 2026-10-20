## Step 1: Lock scope
 
- One-page doc with **must-have / nice-to-have / not-building** lists
- Must-have: monthly payroll across 3 countries, vesting + monthly equity expense, journal entries, anomaly flagging, approval loop
- Pick one demo scenario (e.g. German mid-month hire with a 40,000-option grant: proration, FX, equity expense, approval pause)
- Map every judging criterion and the five core points to a must-have
## Step 2: Synthetic data
 
Tables: `entities`, `employees`, `compensation`, `grants`, `vesting_events`, `payroll_runs`, `payslips`, `journal_entries`, `fx_rates`, `audit_log`, plus `users` (preparer, controller, CFO) and `answer_key` (hidden from the agent).
Pitch line: the company is fictional; the rules, rates, accounting standards and plan structures are real.
 
### 2a. Baseline world (do early, can be small)
- `random.seed(42)` + Faker; fixed "as of" date so output is identical every run
- ~300 employees: ~55% US, 25% UK, 20% DE, each with a **department** (the sponsor doc says payroll entries are "by department and entity")
- ~70% get grants: 4-year vest, 1-year cliff, then monthly
- Strike price steps over time (stand-in for 409A valuations)
- `users` table with distinct roles so segregation of duties can be enforced
- Until you build the full script, 3 hand-made employees (US, UK, DE mid-month hire) are enough for testing
### 2b. History (needs Step 3 done)
- 6-12 months of payroll runs and payslips, produced by calling your real calc functions
- Vesting events and monthly equity expense entries
- Real historical FX rates (ECB or Open Exchange Rates) backfilled into `fx_rates`
- Journal entries for every run, all balanced
### 2c. Mess and proof (do before Step 9)
- Plant **6-10 anomalies**, each logged in `answer_key`. Ideas: salary jump with no compensation change, terminated employee still paid, duplicate employee, grant vesting before the cliff, off-cycle payment, currency mismatch, missing tax field
- **Disagreeing sources**: for a few employees, make HR system, payroll system and grant agreement conflict, and write a policy note stating which is the system of record and why
- Validation checks: headcount, entity split, every grant belongs to a real employee, every entry balances
- One command regenerates the whole DB identically
## Step 3: Deterministic calc layer (no LLM math)
 
- Per-country payroll: US (IRS Pub 15-T, Social Security 6.2%, Medicare 1.45%), UK (PAYE + NI), Germany (income tax + social contributions)
- Proration (mid-month hires and terminations)
- Vesting engine: cliff, monthly, acceleration
- Equity expense: ASC 718 / IFRS 2, straight-line, reverse on forfeiture
- FX converter (`fx.py`, hourly refresh, last-known-rate fallback)
- **Balanced journal entry generator**: debits = credits or it raises an error
- Hand-calculated unit tests, including edge cases (rounding by a cent, forfeiture, mid-month hire)
## Step 4: Agent tools
 
- **Read:** `get_employee`, `list_employees_by_entity`, `get_payroll_history`, `get_grant`, `get_fx_rate`, `search_policies`
- **Compute:** `run_payroll`, `calculate_vesting`, `generate_journal_entries`
- **Act:** `flag_anomaly`, `request_approval`, `post_entries`
- Typed Pydantic schemas; structured JSON output with status and warnings
- `post_entries` is **blocked in code** unless there is an approved `request_approval`
- Every tool call logged to `audit_log`
## Step 5: Agent loop
 
- System prompt with the two roles; all math goes through tools; escalate rather than guess
- Tool-calling loop with a step limit (~25)
- Checklist workflow: validate inputs, run payroll per entity, compare to last month, vesting/expense, entries, flag, approve, post
- Deviation check (~10% vs last month)
- Plain-English rationale for every flag
- Stop branch when a tool fails
## Step 6: Memory
 
- Run log table
- 10-20 policy notes searchable via `search_policies`
- Per-employee payslip history
- Learned corrections: human rejections/edits are shown to the agent on the next run ("what happened in March changes what it does in June")
## Step 7: Approval loop and access limits
 
- Risk rules in code: auto-approve small routine items; always require approval for terminations, off-cycle payments, vesting-term changes
- Pending-approval queue in the UI
- Agent can read everything but only write drafts, flags and approval requests (no money movement)
- Requester can never be the approver (segregation of duties)
- Append-only audit trail
- Guardrail tests, including prompt injection in free-text note fields
## Step 8: UI
 
- Run timeline with expandable reasoning
- Approvals queue
- Journal entries table
- Summary header
- Streamlit for speed, or React + FastAPI for polish
- Stream progress; severity colors
## Step 9: Testing
 
- Compare flags to the hidden answer key: caught / missed / false positives
- Cross-check 10+ payslips and equity figures by hand
- Failure modes: missing data, FX unavailable, API timeout, malformed input
- Fix the top 3-5 issues
- Demo scenario must pass **5 runs in a row**
## Step 10: Demo prep
 
- Criteria-to-demo table
- 2-3 minute script: problem, live run, anomaly catch, approval, audit trail
- Open with the "moment" (catches something others missed, recovers from a failure, or defends a decision)
- Quote catch rate, time saved, entries produced
- Backup video
- Prepared answer to "is this real data?" (what's real, what's simulated, how you know)
- Prepare for judges trying their own scenario live
- Rehearse 3 times, including "what if the agent is wrong?"
## Time split (36-48h hackathon)
 
Steps 1-2 ~15%, Step 3 ~20%, Steps 4-5 ~20%, Steps 6-7 ~15%, Step 8 ~10%, Steps 9-10 ~20%.
**Cut order if short on time:** extra countries, polished UI, memory. Keep the calculations, the approval loop and one flawless demo.
 
## Sponsor judging questions (keep in view)
 
1. **Is it real enough?** Realistic data, messy problem, proof it works
2. **Would you hire it?** Limited access, clean record of work, knows when to ask
3. **Did we see something new?**
4. **The moment**
 