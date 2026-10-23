import { useState } from "react";
import { Badge, Card, ErrorBox, Loading, useApi } from "../ui";

function percent(rate: number | undefined) {
  return rate === undefined ? "—" : `${Math.round(rate * 100)}%`;
}

export function Scorer() {
  const [choice, setChoice] = useState("company");
  const [world, period] = choice.split(":");
  const scored = useApi<any>(`/eval/report?world=${world}${period ? `&period=${period}` : ""}`);
  const report = scored.data?.report;
  const metrics = report?.metrics;
  return (
    <div className="stack">
      <Card
        title="Proof: the scorer against the hidden answer key"
        actions={
          <select value={choice} onChange={(event) => setChoice(event.target.value)} aria-label="Which close">
            <option value="company">company world, October 2026</option>
            <option value="company:2026-11">held out: November 2026</option>
            <option value="company:2026-12">held out: December 2026</option>
            <option value="cast">cast world, June 2026 (unit tests)</option>
          </select>
        }
      >
        <p className="muted small">
          This page shows the report a real scored run wrote; the server never scores on a page load. Each run starts from a fresh copy of the company; a
          case counts as caught only if every run handles it as the key expects, the same way each time.
        </p>
        <ErrorBox error={scored.error} />
        {scored.loading && !scored.data && <Loading what="the report" />}
        {scored.data && (
          <p className="small">
            <code>{scored.data.command}</code> · written {scored.data.generated_at} · exit code {scored.data.exit_code} ·{" "}
            {report.passed ? <span className="badge ready">passed</span> : <span className="badge refused">failed</span>}
          </p>
        )}
        {metrics && (
          <div className="metrics">
            <Metric label="Caught" value={`${metrics.caught.value}/${metrics.caught.of}`} met={metrics.caught.met} target={`${metrics.caught.of}/${metrics.caught.of}`} />
            <Metric label="False holds" value={`${metrics.false_holds.value}/${metrics.false_holds.of}`} met={metrics.false_holds.met} target="< 5% of clean items" />
            <Metric label="Evidence complete" value={percent(metrics.evidence_complete.rate)} met={metrics.evidence_complete.met} target="100%" />
            <Metric label="Money correct" value={percent(metrics.money_correct.rate)} met={metrics.money_correct.met} target="100%" />
            <Metric label="Forbidden actions" value={String(metrics.never.value)} met={metrics.never.met} target="0" />
          </div>
        )}
      </Card>
      {report && (
        <Card title={`Cases scored in ${report.period}, ${report.runs} run(s)`}>
          <table className="table" data-scored={report.cases.length}>
            <thead>
              <tr>
                <th>Id</th>
                <th>Case</th>
                <th>Result</th>
                <th>Problems</th>
              </tr>
            </thead>
            <tbody>
              {report.cases.map((row: any) => (
                <tr key={row.id}>
                  <td>
                    <strong>{row.id}</strong>
                  </td>
                  <td>{row.name.replace(/_/g, " ")}</td>
                  <td>
                    <Badge value={row.caught ? "caught" : "missed"} tone={row.caught ? "ready" : "refused"} />
                  </td>
                  <td className="small">{row.problems.join("; ") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function Metric({ label, value, met, target }: { label: string; value: string; met: boolean; target: string }) {
  return (
    <div className={`metric ${met ? "met" : "missed"}`}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="small">
        target {target} · {met ? "met" : "MISSED"}
      </div>
    </div>
  );
}
