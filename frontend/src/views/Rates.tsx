import { useState } from "react";
import { useSession } from "../session";
import { Card, ErrorBox, useApi } from "../ui";

function months(last: string, count: number): string[] {
  const [year, month] = last.split("-").map(Number);
  const out: string[] = [];
  for (let index = 0; index < count; index++) {
    const total = year * 12 + (month - 1) - index;
    out.push(`${Math.floor(total / 12)}-${String((total % 12) + 1).padStart(2, "0")}`);
  }
  return out;
}

export function Rates() {
  const { period } = useSession();
  const [chosen, setChosen] = useState<string | null>(null);
  const month = chosen ?? period ?? "2026-10";
  const rates = useApi<any>(`/fx/rates?period=${month}`);
  return (
    <Card
      title="Exchange rates (real, Open Exchange Rates, USD base)"
      actions={
        <select value={month} onChange={(event) => setChosen(event.target.value)} aria-label="Month">
          {months(period ?? "2026-10", 18).map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      }
    >
      <p className="muted small">A month translates at the end-of-day rate of its last calendar day. A month cannot lock until that rate is stored; nothing books at a guessed rate.</p>
      <ErrorBox error={rates.error} />
      {rates.data && (
        <div data-rates={rates.data.final_rate_stored ? "final" : "waiting"}>
          <p>
            {month} closes at the rate for <strong>{rates.data.month_end}</strong>:{" "}
            {rates.data.final_rate_stored ? <span className="badge ready">stored</span> : <span className="badge pending">not published yet</span>}
          </p>
          {rates.data.reference ? (
            <table className="table">
              <tbody>
                <tr>
                  <td>GBP → USD</td>
                  <td className="num">{rates.data.reference.gbp_usd_reference}</td>
                </tr>
                <tr>
                  <td>EUR → USD</td>
                  <td className="num">{rates.data.reference.eur_usd_reference}</td>
                </tr>
                <tr>
                  <td>Rate date</td>
                  <td>{rates.data.reference.rate_date}</td>
                </tr>
                <tr>
                  <td>Source</td>
                  <td>{rates.data.reference.rate_source}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="pending">No reference rate for {month} yet. Drafts that need it wait; sizing may use a named fallback.</p>
          )}
          {rates.data.latest_stored && (
            <p className="muted small">
              Latest stored rate: {rates.data.latest_stored.date} ({rates.data.latest_stored.final ? "final" : "intraday"}, {rates.data.latest_stored.source})
            </p>
          )}
        </div>
      )}
    </Card>
  );
}
