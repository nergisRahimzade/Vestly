import { useState } from "react";
import { api, post } from "../api";
import { can, useSession } from "../session";
import { Badge, Card, ErrorBox, Hash, Loading, useApi } from "../ui";
import { EmployeeHistory } from "./History";

export function CaseDetail({ id }: { id: string }) {
  const { me } = useSession();
  const timeline = useApi<any>(`/cases/${id}`);
  const events = useApi<any[]>(can(me, "audit.read") ? `/audit/events?case_id=${id}&limit=1000` : null);
  const reload = () => {
    void timeline.reload();
    void events.reload();
  };
  if (timeline.error) return <ErrorBox error={timeline.error} />;
  if (!timeline.data) return <Loading what="the case" />;
  const row = timeline.data;
  const finding = row.finding ?? {};
  const isPerson = /^NL-/.test(row.subject_number);

  return (
    <div className="stack" data-case-detail={row.key}>
      <p>
        <a href="#/cases">← All cases</a>
      </p>
      <Card
        title={
          <>
            {row.subject_name} <span className="muted">· {row.kind.replace(/_/g, " ")}</span>
          </>
        }
        actions={
          <>
            <Badge value={row.state} />
            <Badge value={row.outcome} />
          </>
        }
      >
        <p className="muted small">
          {row.key} · {row.entity_code} · worked by {row.seat} · opened {row.opened_on} · reason <code>{row.reason_code}</code>
        </p>
        <p className="explanation">{finding.explanation}</p>
        {finding.trusted_value && (
          <p>
            Trusted value: <strong>{finding.trusted_value}</strong> (authority row <code>{finding.authority_row}</code>)
            {finding.draft_allowed === false && <span className="badge held">no draft until answered</span>}
          </p>
        )}
        {me?.role === "controller" && <RunAgent caseId={row.id} onDone={reload} />}
      </Card>

      {finding.ranking?.length > 0 && (
        <Card title="Sources that disagree, ranked by the policy">
          <table className="table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Source</th>
                <th>Says</th>
                <th>Why this rank</th>
              </tr>
            </thead>
            <tbody>
              {finding.ranking.map((item: any) => (
                <tr key={item.rank} className={item.rank === 1 ? "trusted" : ""}>
                  <td>{item.rank === 1 ? "1 · trusted" : item.rank}</td>
                  <td>{item.source}</td>
                  <td>{item.claim}</td>
                  <td className="small">{item.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <Card title="What the decision rests on">
        <ul className="basis">
          {(finding.basis ?? []).map((item: any, index: number) => (
            <Basis key={index} item={item} />
          ))}
        </ul>
        {finding.details?.unverified?.length > 0 && <p className="small">Not verified: {finding.details.unverified.join(", ")}</p>}
      </Card>

      <Card title="Timeline">
        <ol className="timeline">
          {row.steps.map((step: any) => (
            <li key={step.seq}>
              <span className="muted small">{step.on}</span> <Badge value={step.to} /> {step.outcome && <Badge value={step.outcome} />}{" "}
              <strong>{step.by}</strong>
              <div className="small">{step.reason}</div>
              <span className="muted small">evidence {step.evidence}</span>
            </li>
          ))}
        </ol>
        {row.questions.map((question: any) => (
          <div key={question.id} className="row">
            <p className="small">
              <strong>Asked {question.asked_to}</strong> on {question.asked_on}, due {question.due_on} · <Badge value={question.status} />
              {question.escalates && <> · escalates to {question.escalates}</>}
            </p>
            <p className="small">{question.question}</p>
            {question.answers.map((answer: any) => (
              <p key={answer.evidence} className="small answer">
                {answer.by} answered <Badge value={answer.answer} /> on {answer.on}: {answer.note} <span className="muted">({answer.evidence})</span>
              </p>
            ))}
          </div>
        ))}
        {row.links.length > 0 && (
          <p className="small">
            Linked: {row.links.map((link: any) => `${link.resource} ${link.number ?? link.id}`).join(", ")}
          </p>
        )}
      </Card>

      {can(me, "audit.read") && <Challenge caseId={row.id} onDone={reload} />}

      {isPerson && can(me, "sources.read_individual") && (
        <Card title={`${row.subject_name}'s history (every read is logged)`}>
          <EmployeeHistory employee={row.subject_number} through={row.period} />
        </Card>
      )}

      {events.data && (
        <Card title={`Evidence rows for this case (${events.data.length})`}>
          <EvidenceTable rows={events.data} />
        </Card>
      )}
    </div>
  );
}

function Basis({ item }: { item: any }) {
  const [doc, setDoc] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  if (item.type === "document") {
    const open = async () => {
      try {
        setDoc(await api(`/world/documents/${item.doc_sha256}`));
      } catch (exc) {
        setError(exc);
      }
    };
    return (
      <li>
        <span className="badge neutral">document</span> {item.title} <Hash value={item.doc_sha256} />
        {item.quote && <blockquote>“{item.quote}”</blockquote>}
        <button className="link" onClick={open}>
          Open the stored document
        </button>
        <ErrorBox error={error} />
        {doc && (
          <div className="document">
            <p className="muted small">
              {doc.system} · {doc.period} · received {doc.received_on} · sha256 {doc.content_hash}
            </p>
            {doc.body === null ? <p className="muted">The body holds one person's pay; your role sees the metadata only.</p> : <pre>{highlight(doc.body, item.quote)}</pre>}
          </div>
        )}
      </li>
    );
  }
  if (item.type === "rule")
    return (
      <li>
        <span className="badge neutral">rule</span> <a href={`#/policy#${item.id}`}>{item.id}</a>
      </li>
    );
  if (item.type === "precedent")
    return (
      <li>
        <span className="badge neutral">precedent</span> <a href="#/policy">{item.id}</a>, decided by {item.decided_by} on {item.on}
      </li>
    );
  return (
    <li>
      <span className="badge neutral">{item.type}</span> <code>{JSON.stringify(item)}</code>
    </li>
  );
}

function highlight(body: string, quote?: string) {
  if (!quote || !body.includes(quote)) return body;
  const at = body.indexOf(quote);
  return (
    <>
      {body.slice(0, at)}
      <mark>{quote}</mark>
      {body.slice(at + quote.length)}
    </>
  );
}

function RunAgent({ caseId, onDone }: { caseId: string; onDone: () => void }) {
  const [run, setRun] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setRun(await post(`/agent/cases/${caseId}/run`));
      onDone();
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="agent-run">
      <button onClick={start} disabled={busy}>
        {busy ? "Vestly is working the case…" : "Have Vestly work this case"}
      </button>
      <ErrorBox error={error} />
      {run && <AgentRun run={run} />}
    </div>
  );
}

export function AgentRun({ run }: { run: any }) {
  return (
    <div className="run" data-run-status={run.status}>
      <p className="small">
        Run {run.run_id?.slice(0, 8)} · <Badge value={run.status} /> · claim checker: {run.problems?.length ? "blocked" : "every claim backed"}
      </p>
      <p className="shown">{run.shown}</p>
      {run.problems?.map((problem: string) => (
        <p key={problem} className="error small">
          {problem}
        </p>
      ))}
      <p className="muted small">
        Tools: {run.tools?.map((tool: any) => `${tool.tool} ${tool.status} (${tool.evidence_id})`).join(" · ")}
      </p>
    </div>
  );
}

function Challenge({ caseId, onDone }: { caseId: string; onDone: () => void }) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const ask = async () => {
    setError(null);
    try {
      setAnswer(await post(`/cases/${caseId}/challenge`, { question }));
      onDone();
    } catch (exc) {
      setError(exc);
    }
  };
  return (
    <Card title="Challenge this decision">
      <p className="muted small">The answer comes only from this decision's record. Every citation, figure and quote is checked; the decision does not move.</p>
      <div className="form-row">
        <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="e.g. She has been here four years, just run it." />
        <button className="primary" onClick={ask} disabled={!question.trim()}>
          Challenge
        </button>
      </div>
      <ErrorBox error={error} />
      {answer && <ChallengeAnswer answer={answer} />}
    </Card>
  );
}

export function ChallengeAnswer({ answer }: { answer: any }) {
  return (
    <div className="challenge" data-blocked={String(answer.blocked)}>
      <p>
        {answer.decision_stands ? <span className="badge held">decision stands</span> : <span className="badge pending">controller asked</span>}{" "}
        {answer.blocked && <span className="badge refused">answer blocked: shown from the record</span>} still{" "}
        <Badge value={answer.decision.state} /> <Badge value={answer.decision.outcome} />
      </p>
      <p className="shown">{answer.shown}</p>
      <p className="muted small">Cites {answer.citations.join(", ")}</p>
      {answer.problems.map((problem: string) => (
        <p key={problem} className="error small">
          {problem}
        </p>
      ))}
    </div>
  );
}

export function EvidenceTable({ rows }: { rows: any[] }) {
  return (
    <table className="table evidence">
      <thead>
        <tr>
          <th>Id</th>
          <th>Day</th>
          <th>Actor</th>
          <th>Action</th>
          <th>Reason</th>
          <th>Hash</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} data-action={row.action}>
            <td>
              <code>{row.id}</code>
            </td>
            <td className="small">{row.sim_at}</td>
            <td>{row.actor}</td>
            <td>
              <details>
                <summary>{row.action}</summary>
                <pre className="small">{JSON.stringify({ payload: row.payload, basis: row.basis, unverified: row.unverified, supersedes: row.supersedes }, null, 2)}</pre>
              </details>
            </td>
            <td className="small">{row.reason_code}</td>
            <td>
              <Hash value={row.hash} length={10} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
