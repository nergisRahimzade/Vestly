import { useState } from "react";
import { Person } from "../api";
import { useSession } from "../session";
import { ErrorBox, Loading, useApi } from "../ui";

const ROLE_NOTES: Record<string, string> = {
  controller: "Approves above the limit, rules precedents, posts, reopens a locked month.",
  finance_mgr: "Approves entries up to 25,000.00 USD.",
  treasury: "Releases funding to the bank.",
  hr_ops: "Owns employment dates and the bank master; logs call-backs.",
  hr_comp: "Keys pay changes (and so cannot approve the run that pays them).",
  legal: "Holds signed grants and board approvals.",
  payroll_provider: "Runs the registers.",
  dept_head: "Sees department totals, never one person's pay.",
  auditor: "Reads everything, changes nothing; may challenge a decision.",
  none: "Holds no rights: watch every door refuse.",
};

export function Login() {
  const { login } = useSession();
  const people = useApi<Person[]>("/auth/identities");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const pick = async (handle: string) => {
    setBusy(handle);
    try {
      await login(handle);
    } catch (exc) {
      setError(exc);
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="login">
      <h1>
        Vestly <span className="muted">· Northlight Labs</span>
      </h1>
      <p className="lead">
        Vestly fills the payroll accountant and equity compensation accountant seats for a three-entity startup. It drafts every
        entry and funding request, and a person still approves it.
      </p>
      <p className="muted small">
        Pick who you are. The server issues that person's token and decides their role; the UI never sends one. Nobody signs in
        as Vestly.
      </p>
      <ErrorBox error={error ?? people.error} />
      {people.loading && <Loading what="people" />}
      <div className="people">
        {people.data?.map((person) => (
          <button key={person.handle} className="person" onClick={() => pick(person.handle)} disabled={busy !== null} data-handle={person.handle}>
            <strong>{person.display_name}</strong>
            <span className="badge neutral">{person.role.replace(/_/g, " ")}</span>
            <span className="muted small">{person.handle}</span>
            <span className="small">{ROLE_NOTES[person.role] ?? ""}</span>
          </button>
        ))}
      </div>
    </main>
  );
}
