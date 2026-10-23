from app.auth.tokens import token_for


def as_user(handle: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(handle)}"}


CONTROLLER = as_user("dana.ruiz")
PAYROLL_AGENT = as_user("vestly.payroll")
EQUITY_AGENT = as_user("vestly.equity")
# Most older tests draft payroll or call generic agent routes; the payroll seat stands in for "the agent".
AGENT = PAYROLL_AGENT
FINANCE_MGR = as_user("chris.lam")
HR_OPS = as_user("lee.park")
HR_COMP = as_user("sam.kerr")
AUDITOR = as_user("riley.auditor")
STRANGER = as_user("stranger")
PROVIDER = as_user("payroll.provider")
