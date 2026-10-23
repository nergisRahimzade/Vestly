"""Who can call Vestly. The server owns this list; a caller cannot name its own role."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Actor:
    handle: str
    role: str


# handle, display name, role, department (department heads only)
IDENTITIES: tuple[tuple[str, str, str, str | None], ...] = (
    # Vestly works under one identity per seat. Neither can approve, post, release or reopen.
    ("vestly.payroll", "Vestly, payroll accountant seat", "agent", None),
    ("vestly.equity", "Vestly, equity compensation accountant seat", "agent", None),
    ("dana.ruiz", "Dana Ruiz", "controller", None),
    ("chris.lam", "Chris Lam", "finance_mgr", None),
    ("lee.park", "Lee Park", "hr_ops", None),
    ("sam.kerr", "Sam Kerr", "hr_comp", None),
    ("alex.moreau", "Alex Moreau", "treasury", None),
    ("jordan.hayes", "Jordan Hayes", "treasury", None),
    ("morgan.li", "Morgan Li", "legal", None),
    ("payroll.provider", "Payroll provider", "payroll_provider", None),
    ("head.engineering", "Head of Engineering", "dept_head", "Engineering"),
    ("head.product", "Head of Product", "dept_head", "Product"),
    ("head.gtm", "Head of GTM", "dept_head", "GTM"),
    ("head.ga", "Head of G&A", "dept_head", "G&A"),
    ("riley.auditor", "Riley Auditor", "auditor", None),
    ("stranger", "Stranger", "none", None),
)

CONTROLLER_HANDLE = "dana.ruiz"
PAYROLL_SEAT = "vestly.payroll"
EQUITY_SEAT = "vestly.equity"
SEATS = {PAYROLL_SEAT: "payroll", EQUITY_SEAT: "equity"}
