import { useMemo, useState } from "react";
import { useSession } from "../session";
import { Badge, Card, ErrorBox, Loading, useApi } from "../ui";

export type CaseRow = {
  id: string;
  key: string;
  period: string;
  kind: string;
  subject_number: string;
  subject_name: string;
  entity_code: string;
  seat: string;
  state: string;
  outcome: string | null;
  reason_code: string | null;
  opened_on: string;
};

export function Cases() {
  const { period } = useSession();
  const cases = useApi<CaseRow[]>(period ? `/cases?period=${period}` : null);
  const [filter, setFilter] = useState("all");
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const row of cases.data ?? []) out[row.outcome ?? "none"] = (out[row.outcome ?? "none"] ?? 0) + 1;
    return out;
  }, [cases.data]);
  const rows = (cases.data ?? []).filter((row) => filter === "all" || row.outcome === filter);

  return (
    <Card
      title={`Cases for ${period}`}
      actions={
        <>
          <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Filter by outcome">
            <option value="all">All outcomes ({cases.data?.length ?? 0})</option>
            {Object.entries(counts).map(([outcome, count]) => (
              <option key={outcome} value={outcome}>
                {outcome.replace(/_/g, " ")} ({count})
              </option>
            ))}
          </select>
          <button onClick={cases.reload}>Refresh</button>
        </>
      }
    >
      <ErrorBox error={cases.error} />
      {cases.loading && !cases.data && <Loading what="cases" />}
      {cases.data?.length === 0 && <p className="muted">No cases yet: the close for {period} has not run. Run it from The moment.</p>}
      <table className="table cases">
        <thead>
          <tr>
            <th>Who</th>
            <th>What</th>
            <th>Entity</th>
            <th>Seat</th>
            <th>State</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="clickable" onClick={() => (window.location.hash = `#/cases/${row.id}`)} data-case={row.key}>
              <td>
                <strong>{row.subject_name}</strong> <span className="muted small">{row.subject_number}</span>
              </td>
              <td>
                {row.kind.replace(/_/g, " ")}
                <div className="muted small">{row.reason_code}</div>
              </td>
              <td>{row.entity_code}</td>
              <td className="small">{row.seat}</td>
              <td>
                <Badge value={row.state} />
              </td>
              <td>
                <Badge value={row.outcome} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
