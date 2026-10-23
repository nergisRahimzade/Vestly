import { useState } from "react";
import { can, useSession } from "../session";
import { Badge, Card, ErrorBox, Loading, Money, useApi } from "../ui";

type Span = { value: string; from: string; to: string };

function Spans({ label, spans }: { label: string; spans: Span[] }) {
  return (
    <div className="spans">
      <h3>{label}</h3>
      {spans.length === 0 && <p className="muted small">none</p>}
      <ul>
        {spans.map((span, index) => (
          <li key={index}>
            <strong>{span.value}</strong> <span className="muted small">{span.from} → {span.to}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EmployeeHistory({ employee, through }: { employee: string; through?: string }) {
  const history = useApi<any>(`/memory/employees/${employee}${through ? `?through=${through}` : ""}`);
  if (history.error) return <ErrorBox error={history.error} />;
  if (!history.data) return <Loading what="history" />;
  const data = history.data;
  const unpaid = data.payments.filter((payment: any) => !payment.cleared);
  return (
    <div className="history" data-employee={employee}>
      <p>
        <strong>{data.legal_name}</strong> ({data.employee_number}) · hired {data.hire_date} · <Badge value={data.status} />
        {data.termination_date && <> · left {data.termination_date}</>} · {data.months_in_history} months on file ({data.history_from} to{" "}
        {data.history_to}, {data.years_of_history} years)
      </p>
      <div className="grid3">
        <Spans label="Entity and department" spans={data.entity_and_department} />
        <Spans label="HR monthly salary" spans={data.hr_monthly_salary} />
        <Spans label="Bank accounts" spans={data.bank_accounts} />
      </div>
      <h3>Approved pay changes</h3>
      {data.approved_pay_changes.length === 0 ? <p className="muted small">none</p> : <pre className="small">{JSON.stringify(data.approved_pay_changes, null, 2)}</pre>}
      <h3>Call-backs</h3>
      {data.call_backs.length === 0 ? (
        <p className="muted small">none</p>
      ) : (
        <ul>
          {data.call_backs.map((call: any, index: number) => (
            <li key={index} className="small">
              {call.called_on}: account {call.account} <Badge value={call.outcome} tone={call.outcome === "denied" ? "refused" : "ready"} />
            </li>
          ))}
        </ul>
      )}
      <h3>Rulings that cover them</h3>
      <ul>
        {data.rulings.map((ruling: any) => (
          <li key={ruling.id} className="small">
            <strong>{ruling.id}</strong> ({ruling.rule}, {ruling.decided_by}, from {ruling.valid_from}): {ruling.ruling}
          </li>
        ))}
      </ul>
      <h3>Earlier cases</h3>
      {data.earlier_cases.length === 0 ? <p className="muted small">none</p> : <pre className="small">{JSON.stringify(data.earlier_cases, null, 2)}</pre>}
      <details>
        <summary>
          Pay received, {data.pay_received.length} months · {data.payments.length} payments ({unpaid.length} not cleared)
        </summary>
        <table className="table">
          <thead>
            <tr>
              <th>Month</th>
              <th>Entity</th>
              <th>Department</th>
              <th className="num">Gross</th>
              <th className="num">Net</th>
              <th>Account</th>
            </tr>
          </thead>
          <tbody>
            {data.pay_received.map((row: any) => (
              <tr key={`${row.period}-${row.entity_code}`}>
                <td>{row.period}</td>
                <td>{row.entity_code}</td>
                <td>{row.department}</td>
                <td className="num">
                  <Money amount={row.gross} currency={row.currency} />
                </td>
                <td className="num">
                  <Money amount={row.net} />
                </td>
                <td>{row.account}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}

export function History() {
  const { me } = useSession();
  const [employee, setEmployee] = useState("NL-US-001");
  const [shown, setShown] = useState<string | null>(null);
  const [entity, setEntity] = useState("US");
  const [department, setDepartment] = useState("Engineering");
  const baseline = useApi<any>(`/memory/departments/${entity}/${encodeURIComponent(department)}`);
  return (
    <div className="stack">
      <Card title="One employee's memory">
        {can(me, "sources.read_individual") ? (
          <>
            <div className="form-row">
              <input value={employee} onChange={(event) => setEmployee(event.target.value)} aria-label="Employee number" />
              <button className="primary" onClick={() => setShown(employee)}>
                Show history
              </button>
            </div>
            <p className="muted small">One person's pay: shown to the controller and the auditor only (PRV-1), and every read goes into the evidence log.</p>
            {shown && <EmployeeHistory key={shown} employee={shown} />}
          </>
        ) : (
          <p className="muted">Your role sees department totals, never one person's pay (PRV-1).</p>
        )}
      </Card>
      <Card title="What is normal in a department">
        <div className="form-row">
          <select value={entity} onChange={(event) => setEntity(event.target.value)} aria-label="Entity">
            {["US", "UK", "DE"].map((code) => (
              <option key={code}>{code}</option>
            ))}
          </select>
          <select value={department} onChange={(event) => setDepartment(event.target.value)} aria-label="Department">
            {["Engineering", "Product", "GTM", "G&A"].map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </div>
        <ErrorBox error={baseline.error} />
        {baseline.data && (
          <div>
            <p>
              {baseline.data.months.length} months learned ({baseline.data.months[0]} to {baseline.data.months[baseline.data.months.length - 1]}).
              Monthly gross band <Money amount={baseline.data.monthly_gross_band?.low} /> – <Money amount={baseline.data.monthly_gross_band?.high} /> (median{" "}
              <Money amount={baseline.data.monthly_gross_band?.median} />), employer contribution rate {baseline.data.employer_contribution_rate}.
            </p>
            {baseline.data.months_hidden_small_group.length > 0 && (
              <p className="small">Hidden (fewer than three people): {baseline.data.months_hidden_small_group.join(", ")}</p>
            )}
            {baseline.data.observation && (
              <p className="muted small">
                Recorded as observation {baseline.data.observation.id} as of {baseline.data.observation.as_of}, evidence {baseline.data.observation.evidence_id}.
              </p>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
