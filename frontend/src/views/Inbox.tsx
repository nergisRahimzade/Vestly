import { useState } from "react";
import { post } from "../api";
import { useSession } from "../session";
import { Badge, Card, ErrorBox, Loading, Money, useApi } from "../ui";

type Approval = {
  id: string;
  kind: string;
  entity_code: string;
  period: string;
  requested_by: string;
  requested_on: string;
  amount: string;
  currency: string;
  amount_usd: string;
  approvals_needed: number;
  needs_controller: boolean;
  status: string;
  due_on: string;
  escalated: boolean;
  binding: any;
  binding_hash: string;
  decisions: { by: string; role: string; decision: string; note: string | null; on: string }[];
};

type Question = { id: string; case_id: string; question: string; asked_on: string; due_on: string };

export function Inbox() {
  const approvals = useApi<Approval[]>("/approvals/inbox");
  const questions = useApi<Question[]>("/questions/inbox");
  const { me } = useSession();
  return (
    <div className="stack">
      <Card title={`Approvals waiting for ${me?.display_name}`} actions={<button onClick={approvals.reload}>Refresh</button>}>
        <ErrorBox error={approvals.error} />
        {approvals.loading && !approvals.data && <Loading what="approvals" />}
        {approvals.data?.length === 0 && <p className="muted">Nothing waits for you to approve.</p>}
        {approvals.data?.map((row) => <ApprovalRow key={row.id} row={row} onDone={approvals.reload} />)}
      </Card>
      <Card title="Questions Vestly asked you" actions={<button onClick={questions.reload}>Refresh</button>}>
        <ErrorBox error={questions.error} />
        {questions.data?.length === 0 && <p className="muted">No open questions.</p>}
        {questions.data?.map((row) => <QuestionRow key={row.id} row={row} onDone={questions.reload} />)}
      </Card>
    </div>
  );
}

function ApprovalRow({ row, onDone }: { row: Approval; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<string | null>(null);
  const decide = async (approve: boolean) => {
    setError(null);
    try {
      const done = await post(`/approvals/${row.id}/${approve ? "approve" : "decline"}`, { note: note || null });
      setResult(`${approve ? "Approval recorded" : "Declined"}: request is now ${done.status}.`);
      onDone();
    } catch (exc) {
      setError(exc);
    }
  };
  const lines: any[] = row.binding?.lines ?? [];
  return (
    <article className="row approval" data-approval={row.id}>
      <div className="row-head">
        <strong>{row.kind === "funding_request" ? "Funding release" : `Journal ${row.binding?.number ?? ""}`}</strong>
        <span>
          {row.entity_code} · {row.period}
        </span>
        <Money amount={row.amount} currency={row.currency} />
        {row.currency !== "USD" && (
          <span className="muted small">
            (<Money amount={row.amount_usd} currency="USD" />)
          </span>
        )}
        <Badge value={row.status} />
        {row.needs_controller && <span className="badge pending">LIM-1 · two approvers incl. controller</span>}
        {row.escalated && <span className="badge refused">escalated</span>}
      </div>
      <p className="muted small">
        Requested by {row.requested_by} on {row.requested_on}, due {row.due_on}. Needs {row.approvals_needed} approval
        {row.approvals_needed > 1 ? "s" : ""}. Bound to hash <code className="hash">{row.binding_hash.slice(0, 12)}…</code> — any change to amount,
        payee, account or case voids it (BND-1).
        {row.binding?.booking_key && <> Booking key <code>{row.binding.booking_key}</code>.</>}
      </p>
      <details>
        <summary>{lines.length} line(s)</summary>
        <table className="table">
          <tbody>
            {lines.map((line, index) => (
              <tr key={index}>
                {line.map((cell: string | null, column: number) => (
                  <td key={column} className={column >= 2 ? "num" : ""}>
                    {cell ?? "—"}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
      {row.decisions.length > 0 && (
        <p className="small">
          So far: {row.decisions.map((item) => `${item.by} ${item.decision} on ${item.on}`).join(", ")}
        </p>
      )}
      <div className="form-row">
        <input placeholder="Note (a decline needs a reason)" value={note} onChange={(event) => setNote(event.target.value)} />
        <button className="primary" onClick={() => decide(true)}>
          Approve
        </button>
        <button onClick={() => decide(false)}>Decline</button>
      </div>
      <ErrorBox error={error} />
      {result && <p className="ok">{result}</p>}
    </article>
  );
}

function QuestionRow({ row, onDone }: { row: Question; onDone: () => void }) {
  const { me } = useSession();
  const [text, setText] = useState("");
  const [draft, setDraft] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);

  const read = async () => {
    setError(null);
    try {
      setDraft(await post(`/questions/${row.id}/reply`, { text }));
    } catch (exc) {
      setError(exc);
    }
  };
  const confirm = async (accept: boolean) => {
    try {
      const timeline = await post(`/questions/replies/${draft.id}/confirm`, { accept });
      setDone(accept ? `Counted. The case is now ${timeline.state} (${timeline.outcome ?? "no outcome"}).` : "Rejected: the reading counts for nothing; answer again.");
      setDraft(null);
      onDone();
    } catch (exc) {
      setError(exc);
    }
  };
  const answer = async (value: string) => {
    try {
      const timeline = await post(`/questions/${row.id}/answer`, { answer: value, note: text || null });
      setDone(`Answered ${value}. The case is now ${timeline.state}.`);
      onDone();
    } catch (exc) {
      setError(exc);
    }
  };

  return (
    <article className="row question" data-question={row.id}>
      <p>{row.question}</p>
      <p className="muted small">
        Asked {row.asked_on}, due {row.due_on}. <a href={`#/cases/${row.case_id}`}>Open the case</a>
      </p>
      <textarea placeholder="Answer in your own words" value={text} onChange={(event) => setText(event.target.value)} rows={2} />
      <div className="form-row">
        <button className="primary" onClick={read} disabled={!text.trim()}>
          Send reply
        </button>
        {me?.role === "controller" && (
          <>
            <button onClick={() => answer("confirmed")}>Confirm</button>
            <button onClick={() => answer("declined")} disabled={!text.trim()}>
              Decline (note above)
            </button>
          </>
        )}
      </div>
      {draft && (
        <div className="reading" data-reading={draft.status}>
          <p>
            Vestly read your reply as <Badge value={draft.counts_as ?? "unreadable"} /> — it counts only once you confirm.
          </p>
          {draft.reading?.quote && <blockquote>“{draft.reading.quote}”</blockquote>}
          {draft.problems.map((problem: string) => (
            <p key={problem} className="error small">
              {problem}
            </p>
          ))}
          {draft.status === "proposed" && (
            <div className="form-row">
              <button className="primary" onClick={() => confirm(true)}>
                That is what I meant
              </button>
              <button onClick={() => confirm(false)}>No, that is not it</button>
            </div>
          )}
        </div>
      )}
      <ErrorBox error={error} />
      {done && <p className="ok">{done}</p>}
    </article>
  );
}
