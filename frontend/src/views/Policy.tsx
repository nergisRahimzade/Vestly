import { can, useSession } from "../session";
import { Card, ErrorBox, Hash, Loading, Money, useApi } from "../ui";

export function Policy() {
  const { me } = useSession();
  const policy = useApi<any>("/policy");
  const precedents = useApi<any[]>(can(me, "precedent.read") ? "/precedents" : null);
  if (policy.error) return <ErrorBox error={policy.error} />;
  if (!policy.data) return <Loading what="the policy" />;
  const data = policy.data;
  return (
    <div className="stack">
      <Card title={`Policy ${data.version}`}>
        <p className="muted small">
          Controls live outside the model, in this file. The model only sees the outcome: allowed, or refused with a rule id. Hash <Hash value={data.content_hash} length={16} />
        </p>
        <div className="grid3">
          <div>
            <h3>Materiality</h3>
            <Money amount={data.materiality_absolute} currency={data.materiality_currency} />
          </div>
          <div>
            <h3>Approval limit (LIM-1)</h3>
            <Money amount={data.approval_limit} currency={data.approval_limit_currency} />
          </div>
          <div>
            <h3>Call-back window (BNK-1)</h3>
            {data.callback_window_days} days
          </div>
          <div>
            <h3>FX band</h3>
            {data.fx_band}
          </div>
          <div>
            <h3>Contribution rates</h3>
            {Object.entries(data.contributions).map(([code, rate]) => (
              <div key={code} className="small">
                {code}: {String(rate)}
              </div>
            ))}
          </div>
        </div>
      </Card>
      <Card title="Rules">
        <table className="table">
          <tbody>
            {Object.entries(data.rules).map(([id, text]) => (
              <tr key={id} id={id}>
                <td>
                  <strong>{id}</strong>
                </td>
                <td>{String(text)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Which source to trust, per fact">
        <table className="table">
          <thead>
            <tr>
              <th>Fact</th>
              <th>Authority</th>
              <th>Ranking</th>
            </tr>
          </thead>
          <tbody>
            {data.facts.map((fact: any) => (
              <tr key={fact.fact}>
                <td>
                  <code>{fact.fact}</code>
                  {fact.email_is_source === false && <div className="muted small">email is never a source</div>}
                </td>
                <td>{fact.authority}</td>
                <td className="small">
                  {fact.ranking.map((source: any, index: number) => (
                    <div key={index}>
                      {index + 1}. <strong>{source.source}</strong> — {source.reason}
                    </div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="Who may do what">
        <table className="table">
          <tbody>
            {Object.entries(data.roles).map(([role, actions]) => (
              <tr key={role}>
                <td>
                  <strong>{role}</strong>
                </td>
                <td className="small">{(actions as string[]).join(", ") || "nothing"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {precedents.data && (
        <Card title="Precedents (never deleted; overruled by a newer one)">
          <table className="table" data-precedents={precedents.data.length}>
            <thead>
              <tr>
                <th>Id</th>
                <th>Rule</th>
                <th>Scope</th>
                <th>Ruling</th>
                <th>By</th>
                <th>From</th>
              </tr>
            </thead>
            <tbody>
              {precedents.data.map((row) => (
                <tr key={row.id} data-precedent={row.id}>
                  <td>
                    <strong>{row.id}</strong>
                  </td>
                  <td>{row.rule}</td>
                  <td>
                    <code>{Object.keys(row.scope).length ? JSON.stringify(row.scope) : "whole rule"}</code>
                  </td>
                  <td className="small">{row.ruling}</td>
                  <td>{row.decided_by}</td>
                  <td>
                    {row.valid_from}
                    {row.expires && ` – ${row.expires}`}
                    {row.overrules && <div className="small">overrules {row.overrules}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
