import { useState } from "react";
import { api } from "../api";
import { Card, ErrorBox, useApi } from "../ui";
import { EvidenceTable } from "./CaseDetail";

const ACTIONS = ["", "refused", "false_claim", "individual_read", "signed_in", "precedent_ruled", "funding_sent", "callback_recorded", "challenge_answered", "approval_given"];

export function Evidence() {
  const [action, setAction] = useState("refused");
  const events = useApi<any[]>(`/audit/events?limit=1000${action ? `&action=${action}` : ""}`);
  const [check, setCheck] = useState<any>(null);
  const [error, setError] = useState<unknown>(null);
  const verify = async () => {
    try {
      setCheck(await api("/audit/verify"));
    } catch (exc) {
      setError(exc);
    }
  };
  return (
    <div className="stack">
      <Card title="The evidence log" actions={<button onClick={verify}>Verify the hash chain</button>}>
        <p className="muted small">Append-only. Every row carries the hash of the one before it, so an edit anywhere breaks the chain at that row.</p>
        <ErrorBox error={error} />
        {check && (
          <p className={check.ok ? "ok" : "error"} data-chain={check.ok ? "ok" : "broken"}>
            {check.ok ? `Chain verified: ${check.events} events, unbroken.` : `Broken at ${check.first_broken}: ${check.reason}`}
          </p>
        )}
      </Card>
      <Card
        title="Events"
        actions={
          <select value={action} onChange={(event) => setAction(event.target.value)} aria-label="Filter by action">
            {ACTIONS.map((item) => (
              <option key={item} value={item}>
                {item || "everything (first 1,000)"}
              </option>
            ))}
          </select>
        }
      >
        <ErrorBox error={events.error} />
        {events.data && (events.data.length ? <EvidenceTable rows={events.data} /> : <p className="muted">No {action} events.</p>)}
      </Card>
    </div>
  );
}
