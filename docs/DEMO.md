# Vestly demo script

For the person presenting Vestly: the recorded video (it opens with the moment) and the live demo where judges ask for their own scenario. Times are what the browser run took on the dev machine (`npm run e2e`: the whole moment, close included, in 30–32 seconds).

## Before you start

1. Start fresh: `python -m app.world.reset` from `backend/` (the previous database is kept in `backend/runs/`).
2. `uvicorn app.main:app` from `backend/`; wait for the company world to load (about 40 s).
3. `npm run dev` from `frontend/`; open http://localhost:5173.
4. The scripted model is the default, so the run is the one the scorer measures. Say so if asked: the same run scores 21/21 three times.

## The video: 0:00–1:00, the moment

| Time | On screen | Say |
| --- | --- | --- |
| 0:00 | Login picker. Click **Dana Ruiz · controller**. | "Vestly fills two seats at Northlight Labs: payroll accountant and equity compensation accountant. It drafts every entry; a person still approves it. Nobody can sign in as Vestly." |
| 0:05 | **The moment** → **Run the close**. | "October's close, across three entities and 307 people. Vestly opened every case the sources disagree on." |
| 0:15 | Maya's card: held, the email quote, P-2026-03-001, HR's account. | "Maya's bank change came by email. Email is never a source for bank details; HR's bank master is. Vestly holds it and cites the controller's own ruling from March." |
| 0:22 | **Push back as the controller**. | "Dana pushes back: she's been here four years, just run it." |
| 0:27 | The answer: *decision stands*, citations; *Money to 9911882277: none requested, none paid.* | "Vestly answers only from the record: the email, the March precedent, BNK-1, the policy hash. Every figure and quote is checked before it's shown. The money doesn't move." |
| 0:35 | **HR calls Maya back**: the log shows Lee's call-back, Lee's own words, Vestly's reading, Lee confirming. | "HR calls Maya on the number on file. She never sent it: fraud. Lee's reply counts only once Lee confirms how Vestly read it." |
| 0:43 | Vestly's report: *Proposed a BNK-1 precedent blocking account 9911882277 in every entity*; the new precedent row. | "Vestly proposes blocking that account everywhere; the controller confirms. Now nobody, in any entity, can be paid to it." |
| 0:50 | **Treasury releases**: the reconciliation table, every line *agrees*; *evidence chain unbroken*. | "The US batch half-cleared. Treasury releases only the two legitimate lines the bank never paid, not Maya's. Funding log, bank and ledger agree to the cent, and every step is in a hash-chained record." |
| 0:58 | **Proof** tab: 21/21, 0 false holds, 100%, 100%, 0. | "Three runs on fresh copies against an answer key Vestly never sees, plus November and December held out: 16/16 and 15/15." |

## After the moment (if there is time)

- **Separation of duties:** in **My inbox** as Dana, the US payroll draft is above 25,000.00 USD, so it shows *LIM-1 · two approvers incl. controller*; approve once and it still waits for the second. On **Proof**, case A13 is the scored run where Vestly drafted a 62,000.00 accrual, tried to approve it itself and was refused (SOD-1). **Evidence**, filtered by `refused`, lists every attempt the controls stopped.
- **Time:** a case page → *history*: four years of one person's pay, accounts and rulings. Cases H01–H08 were found only from the history: Marcus Bell left in 2023 and was paid again in July 2026; Elena Petrova was hired with Marcus's old bank account.
- **Least privilege:** sign in as **Head of Engineering**: **Memory** offers department totals only, never one person's pay (PRV-1). Then **Stranger**: every door refuses.
- **Accountability:** **Evidence** → *Verify the hash chain*; every row carries the hash of the one before it.

## Live: a judge's own scenario

Ask for a kind, a person and a month, then reset with it:

```text
python -m app.world.reset --scenario wrong_contribution:NL-UK-102:2026-10
uvicorn app.main:app
```

Kinds: `ghost_employee` (needs an unknown number such as `NL-US-999`), `bank_change_by_email`, `wrong_contribution`, `duplicate_payment`, `department_miscoded`, `missing_from_register`, `vesting_typo`, `duplicate_grant`. Run the close from **The moment** and open the new case: who it asks, which rule it cites, what it drafts or holds. The test suite plants each kind on a clean person and checks the case it opens and who it asks (a ghost goes to HR, `lee.park`, and nothing is paid to it).

Real people to use: `NL-UK-102` Oscar Zimmer, `NL-US-103` Milo Ueda, `NL-DE-103` Anton Yilmaz, `NL-DE-104` Wanda Nakamura (all clean in October).

## If something goes wrong on stage

- A refusal is a feature: read out the rule id it shows.
- The backend not ready yet: the login page says it can't reach the backend; wait for the world to load.
- Claude mode without API credit: the agent reports the model error by name and hands the case to a person (it never guesses). Switch back to the scripted model (`VESTLY_MODEL_MODE` unset) and restart.
