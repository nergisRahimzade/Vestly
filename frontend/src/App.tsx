import { can, useSession } from "./session";
import { useHashRoute } from "./ui";
import { CaseDetail } from "./views/CaseDetail";
import { Cases } from "./views/Cases";
import { Evidence } from "./views/Evidence";
import { History } from "./views/History";
import { Inbox } from "./views/Inbox";
import { Login } from "./views/Login";
import { Moment } from "./views/Moment";
import { Policy } from "./views/Policy";
import { Rates } from "./views/Rates";
import { Scorer } from "./views/Scorer";

const TABS: { path: string; label: string; needs?: string }[] = [
  { path: "moment", label: "The moment" },
  { path: "inbox", label: "My inbox" },
  { path: "cases", label: "Cases", needs: "audit.read" },
  { path: "evidence", label: "Evidence", needs: "audit.read" },
  { path: "history", label: "Memory" },
  { path: "policy", label: "Policy" },
  { path: "rates", label: "FX rates", needs: "ledger.read" },
  { path: "scorer", label: "Proof", needs: "audit.read" },
];

export function App() {
  const { me, today, period, logout } = useSession();
  const [route] = useHashRoute();
  if (!me) return <Login />;
  const tabs = TABS.filter((tab) => !tab.needs || can(me, tab.needs));
  const [page, id] = route;
  return (
    <div className="shell">
      <header className="top">
        <div className="brand">
          Vestly <span className="muted">· Northlight Labs</span>
        </div>
        <div className="clock small">
          Open month <strong>{period}</strong> · simulated today <strong>{today}</strong>
        </div>
        <div className="me" data-me={me.handle}>
          <strong>{me.display_name}</strong> <span className="badge neutral">{me.role.replace(/_/g, " ")}</span>
          <button className="link" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>
      <nav className="tabs">
        {tabs.map((tab) => (
          <a key={tab.path} href={`#/${tab.path}`} className={page === tab.path ? "active" : ""}>
            {tab.label}
          </a>
        ))}
      </nav>
      <main className="content">
        {page === "moment" && <Moment />}
        {page === "inbox" && <Inbox />}
        {page === "cases" && (id ? <CaseDetail key={id} id={id} /> : <Cases />)}
        {page === "evidence" && <Evidence />}
        {page === "history" && <History />}
        {page === "policy" && <Policy />}
        {page === "rates" && <Rates />}
        {page === "scorer" && <Scorer />}
      </main>
    </div>
  );
}
