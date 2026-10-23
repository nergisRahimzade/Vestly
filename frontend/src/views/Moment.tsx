import { ReactNode, useEffect, useRef, useState } from "react";
import { api, post, signIn } from "../api";
import { useSession } from "../session";
import { Badge, Card, ErrorBox, Money } from "../ui";
import { AgentRun, ChallengeAnswer } from "./CaseDetail";

// The scenario's words. Everything else on this page comes from the server.
const MAYA = "NL-US-001";
const PUSH_BACK = "She has been here four years, just run it.";
const HR_REPLY = "No, she never sent that email. I called her on the number we have on file and she knew nothing about it. Block the account.";

type Beat = { done: boolean; busy: boolean; error: unknown; log: { who: string; what: string }[]; result: any };
const fresh = (): Beat => ({ done: false, busy: false, error: null, log: [], result: null });

/** Sign in as each person through the same picker anyone uses; the server issues the token and decides the role. */
function useActors() {
  const tokens = useRef<Record<string, string>>({});
  return async (handle: string) => {
    if (!tokens.current[handle]) tokens.current[handle] = (await signIn(handle)).token;
    return tokens.current[handle];
  };
}

export function Moment() {
  const { period, refreshClock } = useSession();
  const as = useActors();
  const [beats, setBeats] = useState<Beat[]>([fresh(), fresh(), fresh(), fresh()]);
  const [maya, setMayaState] = useState<any>(null);
  const mayaRef = useRef<any>(null);
  const setMaya = (timeline: any) => {
    mayaRef.current = timeline;
    setMayaState(timeline);
  };
  const [running, setRunning] = useState(false);
  const state = useRef(beats);
  state.current = beats;

  const update = (index: number, change: Partial<Beat>) =>
    setBeats((current) => current.map((beat, at) => (at === index ? { ...beat, ...change } : beat)));
  const say = (index: number, who: string, what: string) =>
    setBeats((current) => current.map((beat, at) => (at === index ? { ...beat, log: [...beat.log, { who, what }] } : beat)));

  const findMaya = async () => {
    const riley = await as("riley.auditor");
    const cases = await api<any[]>(`/cases?period=${period}`, { token: riley });
    const row = cases.find((item) => item.kind === "bank_detail_change" && item.subject_number === MAYA);
    if (!row) return { cases, timeline: null };
    const timeline = await api(`/cases/${row.id}`, { token: riley });
    setMaya(timeline);
    return { cases, timeline };
  };

  useEffect(() => {
    if (period) void findMaya().then(({ cases }) => cases.length && update(0, { done: true, result: { cases, reused: true } }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period]);

  const run = async (index: number, work: () => Promise<any>) => {
    update(index, { busy: true, error: null, log: [] });
    try {
      const result = await work();
      update(index, { busy: false, done: true, result });
      return true;
    } catch (exc) {
      update(index, { busy: false, error: exc });
      return false;
    }
  };

  // 1. June's moment, now October: the close is drafted and Maya's emailed bank change is held.
  const close = () =>
    run(0, async () => {
      const dana = await as("dana.ruiz");
      say(0, "dana.ruiz", `starts the ${period} close; Vestly's seats do the work`);
      const result = await post(`/close?period=${period}`, {}, dana);
      const { cases } = await findMaya();
      return { ...result, cases };
    });

  // 2. The controller pushes back. Vestly answers from the record and does not move the money.
  const pushBack = () =>
    run(1, async () => {
      const dana = await as("dana.ruiz");
      say(1, "dana.ruiz", `“${PUSH_BACK}”`);
      const answer = await post(`/cases/${mayaRef.current.id}/challenge`, { question: PUSH_BACK }, dana);
      const funding = await api<any[]>(`/funding?period=${period}`, { token: await as("riley.auditor") });
      const account = mayaRef.current.finding.details.requested_account;
      const toFraud = funding.flatMap((request) => request.lines).filter((line: any) => line.destination_account === account);
      return { answer, account, toFraud, funding };
    });

  // 3. HR's call-back shows fraud; Vestly proposes blocking the account everywhere; the controller confirms.
  const callBack = () =>
    run(2, async () => {
      const lee = await as("lee.park");
      const dana = await as("dana.ruiz");
      const account = mayaRef.current.finding.details.requested_account;
      say(2, "lee.park", `calls Maya on the number on file and logs the call-back: account ${account} denied`);
      await post("/controls/callbacks", { employee_number: MAYA, account, outcome: "denied", note: "Called Maya on the number on file; she never sent the email." }, lee);
      const question = (await api<any[]>("/questions/inbox", { token: lee })).find((item) => item.case_id === mayaRef.current.id);
      if (!question) throw new Error("No open question to lee.park on Maya's case.");
      say(2, "lee.park", `replies: “${HR_REPLY}”`);
      const reading = await post(`/questions/${question.id}/reply`, { text: HR_REPLY }, lee);
      say(2, "vestly", `reads it as “${reading.counts_as ?? "unreadable"}” and shows lee.park the reading`);
      if (reading.status !== "proposed" || reading.counts_as !== "declined") throw new Error(`Vestly read the reply as ${reading.counts_as}; it does not count.`);
      await post(`/questions/replies/${reading.id}/confirm`, { accept: true }, lee);
      say(2, "lee.park", "confirms the reading; only now does it count");
      say(2, "dana.ruiz", "has Vestly work the case again");
      const agentRun = await post(`/agent/cases/${mayaRef.current.id}/run`, {}, dana);
      const proposal = (await api<any[]>("/questions/inbox", { token: dana })).find((item) => item.question.startsWith("Proposed precedent under BNK-1"));
      if (!proposal) throw new Error("Vestly did not propose a precedent.");
      say(2, "dana.ruiz", "confirms the proposed precedent");
      await post(`/questions/${proposal.id}/answer`, { answer: "confirmed", note: "Fraud confirmed by HR's call-back." }, dana);
      const precedents = await api<any[]>("/precedents", { token: dana });
      const block = precedents.find((row) => row.scope.account === account);
      await findMaya();
      return { reading, agentRun, block };
    });

  // 4. The half-cleared US batch: only the unconfirmed legitimate lines go out; bank, ledger and funding log agree.
  const funding = () =>
    run(3, async () => {
      const alex = await as("alex.moreau");
      const inbox = await api<any[]>("/approvals/inbox", { token: alex });
      const requests = inbox.filter((row) => row.kind === "funding_request" && row.period === period);
      if (!requests.length) throw new Error("No funding request waits for treasury.");
      for (const request of requests) {
        say(3, "alex.moreau", `releases ${request.binding.lines.length} line(s): ${request.binding.lines.map((line: any) => line[0]).join(", ")}`);
        await post(`/approvals/${request.id}/approve`, { note: "Released after checking the bank file." }, alex);
        if (request.needs_controller) {
          say(3, "dana.ruiz", "co-approves above the limit (LIM-1)");
          await post(`/approvals/${request.id}/approve`, { note: "Co-approved." }, await as("dana.ruiz"));
        }
      }
      const riley = await as("riley.auditor");
      const pending = await api<any[]>("/approvals?status=pending", { token: riley });
      const cash = pending.filter((row) => (row.binding.booking_key ?? "").startsWith("funding:cash:"));
      const chris = await as("chris.lam");
      const dana = await as("dana.ruiz");
      for (const row of cash) {
        say(3, "chris.lam", `approves the cash journal ${row.binding.number} (${row.amount} ${row.currency})`);
        await post(`/approvals/${row.id}/approve`, { note: "Cash matches the bank confirmation." }, chris);
        say(3, "dana.ruiz", `posts ${row.binding.number}`);
        await post(`/journal-entries/${row.subject_id}/post`, {}, dana);
      }
      const reconciliations = [];
      for (const request of requests) reconciliations.push(await api(`/funding/${request.subject_id}/reconciliation`, { token: riley }));
      const chain = await api("/audit/verify", { token: riley });
      await refreshClock();
      return { reconciliations, chain };
    });

  const all = async () => {
    setRunning(true);
    try {
      if (!state.current[0].done && !(await close())) return;
      for (const [index, step] of [[1, pushBack], [2, callBack], [3, funding]] as const) {
        if (!state.current[index].done && !(await step())) return;
      }
    } finally {
      setRunning(false);
    }
  };

  const ready = (index: number) => !beats[index].busy && !running && (index === 0 || (beats[index - 1].done && !!maya));

  return (
    <div className="stack moment">
      <Card
        title={`The moment · ${period} close`}
        actions={
          <button className="primary" onClick={all} disabled={running || beats.every((beat) => beat.done)} data-run-all>
            {running ? "Playing…" : "Play the whole moment"}
          </button>
        }
      >
        <p className="muted small">
          Demo director: each step signs in as the named person through the login picker and uses the same doors they would. Vestly acts only under
          its own seats; nothing here can approve as Vestly or skip a control.
        </p>
      </Card>

      <Step index={0} beat={beats[0]} title="1 · The close is drafted, and Maya's emailed bank change is held" onRun={close} ready={ready(0)} label="Run the close">
        {beats[0].result && <CloseSummary result={beats[0].result} />}
        {maya && <MayaHold maya={maya} />}
      </Step>

      <Step index={1} beat={beats[1]} title="2 · The controller pushes back; Vestly answers from the record" onRun={pushBack} ready={ready(1)} label="Push back as the controller">
        {beats[1].result && (
          <>
            <ChallengeAnswer answer={beats[1].result.answer} />
            <p className="ok" data-money-moved={beats[1].result.toFraud.length}>
              Money to {beats[1].result.account}: {beats[1].result.toFraud.length === 0 ? "none requested, none paid." : `${beats[1].result.toFraud.length} line(s)!`}
            </p>
          </>
        )}
      </Step>

      <Step index={2} beat={beats[2]} title="3 · HR's call-back shows fraud; a new precedent blocks the account everywhere" onRun={callBack} ready={ready(2)} label="HR calls Maya back">
        {beats[2].result && (
          <>
            <AgentRun run={beats[2].result.agentRun} />
            {beats[2].result.block && (
              <p className="ok" data-precedent={beats[2].result.block.id}>
                <strong>{beats[2].result.block.id}</strong> ({beats[2].result.block.rule}, scope <code>{JSON.stringify(beats[2].result.block.scope)}</code>,
                decided by {beats[2].result.block.decided_by} from {beats[2].result.block.valid_from}): {beats[2].result.block.ruling}
              </p>
            )}
          </>
        )}
      </Step>

      <Step index={3} beat={beats[3]} title="4 · The half-cleared US batch: only the missing legitimate lines are paid" onRun={funding} ready={ready(3)} label="Treasury releases">
        {beats[3].result?.reconciliations.map((recon: any) => (
          <table key={recon.request} className="table" data-reconciliation={recon.lines.every((line: any) => line.agrees) ? "agrees" : "differs"}>
            <thead>
              <tr>
                <th>Payment</th>
                <th className="num">Funding log</th>
                <th className="num">Bank</th>
                <th className="num">Ledger</th>
                <th>Journal</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {recon.lines.map((line: any) => (
                <tr key={line.payment_id}>
                  <td>{line.payment_id}</td>
                  <td className="num">
                    <Money amount={line.requested} />
                  </td>
                  <td className="num">
                    <Money amount={line.bank} />
                  </td>
                  <td className="num">
                    <Money amount={line.ledger} />
                  </td>
                  <td>{line.journal}</td>
                  <td>{line.agrees ? <Badge value="agrees" /> : <Badge value="differs" tone="refused" />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
        {beats[3].result?.chain && (
          <p className={beats[3].result.chain.ok ? "ok" : "error"}>
            Evidence chain: {beats[3].result.chain.ok ? `${beats[3].result.chain.events} events, unbroken.` : `broken at ${beats[3].result.chain.first_broken}`}
          </p>
        )}
      </Step>
    </div>
  );
}

function Step(props: { index: number; beat: Beat; title: string; label: string; ready: boolean; onRun: () => void; children: ReactNode }) {
  const { beat } = props;
  return (
    <Card
      id={`beat-${props.index + 1}`}
      title={props.title}
      actions={
        beat.done ? (
          <span className="badge ready" data-beat-done={props.index + 1}>
            done
          </span>
        ) : (
          <button onClick={props.onRun} disabled={!props.ready}>
            {beat.busy ? "Working…" : props.label}
          </button>
        )
      }
    >
      {beat.log.length > 0 && (
        <ol className="log">
          {beat.log.map((line, index) => (
            <li key={index}>
              <strong>{line.who}</strong> {line.what}
            </li>
          ))}
        </ol>
      )}
      <ErrorBox error={beat.error} />
      {props.children}
    </Card>
  );
}

function CloseSummary({ result }: { result: any }) {
  const cases: any[] = result.cases ?? [];
  const by = (outcome: string) => cases.filter((row) => row.outcome === outcome).length;
  return (
    <p data-cases={cases.length}>
      {result.reused ? "The close has already run: " : ""}
      {cases.length} cases — {by("held")} held, {by("ready")} ready for approval, {by("rejected_duplicate")} rejected as duplicates, {by("needs_human")} need a person.
      {result.journals && ` ${result.journals.length} journals drafted; ${result.funding_requests.length} funding request(s) wait for treasury.`}{" "}
      <a href="#/cases">See every case</a>
    </p>
  );
}

function MayaHold({ maya }: { maya: any }) {
  const quote = maya.finding.basis.find((item: any) => item.type === "document")?.quote;
  const precedent = maya.finding.basis.find((item: any) => item.type === "precedent");
  return (
    <div className="maya" data-maya-outcome={maya.outcome}>
      <p>
        <strong>{maya.subject_name}</strong> · <Badge value={maya.state} /> <Badge value={maya.outcome} /> · <code>{maya.reason_code}</code> ·{" "}
        <a href={`#/cases/${maya.id}`}>open the case</a>
      </p>
      {quote && <blockquote>The email: “{quote}”</blockquote>}
      <p className="small">
        Trusted: HR bank master <strong>{maya.finding.trusted_value}</strong> (authority row <code>{maya.finding.authority_row}</code>). Rule BNK-1
        {precedent && (
          <>
            {" "}
            · precedent <strong>{precedent.id}</strong>, decided by {precedent.decided_by} on {precedent.on}
          </>
        )}
        .
      </p>
    </div>
  );
}
