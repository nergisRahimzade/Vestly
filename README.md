# Vestly

*Autonomous payroll and stock-grant accounting for a global startup. CalHacks 13.0, Hyphenate Track.*

## One-Line Pitch

Vestly is the finance teammate that turns every payroll run and every stock grant into correct books, across countries and departments, knows exactly who to ask when the systems disagree, and can defend every decision years later.

## Short Description

Northwind Labs (fictional) is a 300-person software startup with a US parent and subsidiaries in the UK and Germany. Employees are paid through a payroll provider, tracked in an HR system, and granted stock that vests over four years. Each system holds part of the truth, and they rarely agree. Vestly fills two seats on the finance team, the **payroll accountant** and the **equity compensation accountant**. Every month it reconciles the HR system, the payroll provider, the equity platform, the bank and the ledger. It drafts the journal entries, catches the silent errors that build up between systems, asks the right human at the right moment, and records the full reasoning behind every call.

## Why We Need It

- **Payroll and equity data lives in at least four systems.** HR knows who works where and since when. Payroll knows what was paid. The equity platform knows what has vested. The ledger knows what was recorded. Nobody checks all four against each other every month.
- **Errors are quiet and compound.** A termination entered three weeks late means overpaid wages and stock expense that keeps accruing for shares the person already lost. Each system looks fine on its own.
- **The work is time-based.** A grant approved in 2023 still creates entries in 2027. A backdated raise in August changes April and May.
- **The data is sensitive.** Payroll cannot be handed to a loose agent with full access. Companies need strict limits, segregation of duties and a clean record.
- **Small finance teams do this by hand,** with spreadsheets and email threads, which is slow and hard to audit.

## What It Does

1. **Ingests** monthly payroll reports, HR changes, equity grant and vesting data, and bank activity.
2. **Reconciles** the sources against each other and explains every difference.
3. **Drafts journal entries** for wages, employer taxes, withholdings, accruals at month-end and monthly stock expense, split by department and legal entity.
4. **Detects exceptions,** such as late terminations, backdated raises, ghost employees, forfeited grants still being expensed, grants with no approval, and vesting schedules that don't match offer letters.
5. **Decides which source to trust and says why,** using a documented source-of-truth policy per fact (for example, employment dates come from HR).
6. **Asks the right person:** HR for dates, the department head for cost centers, legal or the board for grant approval, the payroll provider for tax treatment, the controller for anything touching a closed month.
7. **Waits, reminds and resumes.** Approvals can take days. Vestly holds the item, follows up at the right time, and handles the answer, including a no.
8. **Records everything** in an append-only audit trail that links each entry to its data, rule, approver and reasoning.
9. **Answers questions** from reviewers: why did this number change, who approved this, what did we know at the time.

## Example Scenario

An employee leaves on March 12. HR records it on April 2. Payroll paid through March 15. The equity platform still shows shares vesting, and the ledger still expenses them monthly. Vestly compares all four, concludes the last three days of pay and one month of stock expense are likely wrong, asks HR to confirm the date, shows the controller the proposed correction, and logs the entire chain.

## Team Seats Filled

**Payroll accountant and equity compensation accountant** for a multi-entity startup.

## Tech Stack

*Proposed. Adjust to what the team knows best.*

| Layer | Choice | Why |
| --- | --- | --- |
| Language and API | Python with FastAPI | Fast to build, strong data libraries |
| Agent reasoning | An LLM (Claude or Gemini) with structured tool calling | Reasoning over messy evidence, explanations |
| Orchestration | A state machine (for example LangGraph) over a durable workflow layer (for example Temporal or a Postgres-backed job queue) | Work must survive days of waiting for approvals and restarts |
| Ledger | Postgres with double-entry constraints, or Formance Ledger / Beancount | Entries must always balance; periods can be locked |
| Long-term memory | Postgres plus a vector index for past cases, vendor and policy notes | Past events change future behavior |
| Audit trail | Append-only event log with hash chaining | Tamper-evident, traceable decisions |
| Access control | Role-based permissions, per-tool scopes, separate identities for prepare and approve | Limits and segregation of duties |
| Simulated company | Synthetic generators for HR, payroll, equity, bank data, plus mock APIs (for example Mockoon) | Realistic, messy data with planted errors |
| Simulated people | LLM-played approvers (HR, department head, controller, auditor) with configurable delays and refusals | Tests the ask-and-wait loop |
| Interface | Next.js dashboard and an approver inbox (email or Slack mock) | Live demo, review queue, audit explorer |
| Evaluation | A scripted harness that scores against the planted-error answer key | Proof it works |

## How It Meets the Judging Criteria

### 1. Is it real enough?

- **Realistic data:** synthetic but structured like real HR, payroll and equity exports for three entities, four departments and about 300 employees across several years.
- **Messy problem:** 15 to 20 planted exception types (late terminations, backdated raises, ghost employees, cross-entity work, bonus estimate misses, FX rate mismatches, forfeitures, unapproved grants, vesting typos, acceleration clauses, duplicate grants, option exercises).
- **Proof:** an answer key for every planted error. We report detection rate, false alarms, correct source-of-truth calls, correct routing to the right person, and whether every entry balances. A held-out set of scenarios is kept hidden until the final test.
- **Honesty:** we state clearly what is real (software, ledger rules, controls) and what is simulated (all company data and the approvers).

### 2. Would you hire it?

- **Clear seats:** two defined roles with defined outputs.
- **Limited access:** it reads department-level totals wherever possible, sees individual pay only when needed, drafts entries but cannot post them, and cannot post to a closed period.
- **Clean record:** every action is logged with evidence and reasoning.
- **Knows when to ask:** thresholds, materiality and policy decide when it acts alone and when it escalates.

### 3. Did we see something new?

- A cross-system disagreement problem across HR, payroll, equity and the ledger rather than one system in isolation.
- Payroll and long-vesting equity handled together, so time (a 2023 grant affecting 2027) is central.
- An agent whose access limits are a core feature, not an afterthought.
- *Note:* payroll is on Hyphenate's sample list, so the novelty comes from the combination with equity and from the source-conflict and time-based behavior. We will confirm with the Hyphenate team how this compares with what they have seen.

### 4. The moment

The demo video opens with one of these:

- **Catching what others missed:** departed employees' unvested grants are still being expensed. Vestly shows four sources disagreeing, names the one it trusts and why, and proposes the fix.
- **Defending a decision:** the controller asks why April's stock expense dropped by $41,000. Vestly traces it to three forfeitures, each linked to a termination record and grant document.
- **Recovering:** HR corrects a backdated date. Vestly reopens the affected months through the proper approval path instead of rewriting history.
- **Limits in action:** someone asks it to show everyone's salary, and it declines and offers the aggregate view that meets the legitimate need.

## How It Covers the 5 Core Points

| Core point | How Vestly handles it |
| --- | --- |
| **Sources that disagree** | A source-of-truth policy per fact (employment dates from HR, cash paid from payroll and bank, vesting terms from signed grant documents). When sources conflict, Vestly shows the conflict, picks the trusted source, and states the reason. |
| **Time** | A long-term case memory and a durable workflow engine. Old grants, amendments and past errors are reloaded when relevant. A vendor or process that caused errors before triggers stricter checks later. |
| **Working with others** | A routing map of who owns which question (HR, department heads, legal, payroll provider, controller). Vestly asks at the right moment, sends reminders, handles partial answers, and handles rejection by adjusting its plan. |
| **Limits** | Least-privilege tool scopes, no posting to closed periods, and strict segregation of duties: the identity that prepares an entry cannot approve it, and the person who changes pay cannot approve the payroll run. |
| **Explainable and accountable** | Every decision is stored with its evidence, rule, alternatives considered and approver. A reviewer can start from any journal entry and walk back to the source records, no matter how long ago it was made. |

## Demo Plan

1. Open on the moment (about 20 seconds).
2. Show the company: five systems, three entities, a year of history.
3. Run a month-end live, including a scenario chosen by a judge.
4. Show the approver inbox, a delayed approval, and a rejection being handled.
5. Show the audit explorer tracing an entry back to its root.
6. Show the scorecard against the answer key.

## Scope and Risks

- **Scope:** one company, two seats, one monthly cycle with history, around 10 to 15 exception types done well.
- **Risk, domain accuracy:** we will verify accounting treatments (accrual timing, forfeiture handling, withholding entries) with the Hyphenate team and public references before encoding them.
- **Risk, data realism:** all data is synthetic. We will say so up front.
- **Risk, over-scoping:** payroll is huge. We will cut anything that does not serve the moment.

## Glossary

- **Vesting:** earning stock gradually, often over four years.
- **Cliff:** a waiting period, often one year, before any stock vests.
- **Forfeiture:** unvested stock lost when someone leaves.
- **Accrual:** recording a cost in the month it was earned, even if paid later.
- **Withholding:** money taken from pay and sent to tax authorities.
- **Reconciliation:** confirming two records agree and explaining any difference.
- **Segregation of duties:** no one can both start and approve the same action.
- **Audit trail:** a record of who did what, when and why.

## Open Questions to Confirm at the Hackathon

- Which accounting treatments does Hyphenate expect for forfeitures and backdated changes?
- How do real finance teams route payroll exceptions?
- Are any of the benchmarks usable for payroll or equity scenarios?
- Does a combined payroll and equity scope read as new to the judges?
