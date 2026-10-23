import { ReactNode, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "./api";

export function useApi<T = any>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);
  const reload = useCallback(async () => {
    if (path === null) return;
    setLoading(true);
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (exc) {
      setError(exc as ApiError);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  const exc = error as ApiError;
  return (
    <div className="error" role="alert">
      {exc.rule && <span className="badge refused">{exc.rule}</span>}
      <strong>{exc.status ? `${exc.status} ${exc.code}` : "Error"}</strong> {exc.message}
      {exc.errorId && <div className="muted small">error id {exc.errorId} · {exc.where}</div>}
    </div>
  );
}

const TONES: Record<string, string> = {
  held: "held",
  refused: "refused",
  declined: "refused",
  needs_human: "refused",
  ready: "ready",
  approved: "ready",
  confirmed: "ready",
  posted: "ready",
  agrees: "ready",
  rejected_duplicate: "neutral",
  pending: "pending",
  waiting_on_human: "pending",
  proposed: "pending",
};

export function Badge({ value, tone }: { value: string | null | undefined; tone?: string }) {
  if (!value) return null;
  return <span className={`badge ${tone ?? TONES[value] ?? "neutral"}`}>{value.replace(/_/g, " ")}</span>;
}

export function Card({ title, children, actions, id }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <section className="card" id={id}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return <p className="muted">Loading {what}…</p>;
}

export function Money({ amount, currency }: { amount: string | null | undefined; currency?: string | null }) {
  if (amount === null || amount === undefined) return <span className="muted">—</span>;
  const [whole, cents = "00"] = amount.replace("-", "").split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (
    <span className="money">
      {amount.startsWith("-") ? "−" : ""}
      {grouped}.{cents}
      {currency ? ` ${currency}` : ""}
    </span>
  );
}

export function Hash({ value, length = 12 }: { value: string | null | undefined; length?: number }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <code className="hash" title={value}>
      {value.slice(0, length)}…
    </code>
  );
}

export function useHashRoute(): [string[], (path: string) => void] {
  const read = () => (window.location.hash.replace(/^#\/?/, "") || "moment").split("/");
  const [parts, setParts] = useState<string[]>(read);
  useEffect(() => {
    const onChange = () => setParts(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return [parts, (path: string) => (window.location.hash = `#/${path}`)];
}
