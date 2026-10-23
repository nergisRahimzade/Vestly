from app.auth.tokens import token_for


def as_user(handle: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(handle)}"}


CONTROLLER = as_user("dana.ruiz")
AGENT = as_user("vestly")
AUDITOR = as_user("riley.auditor")
STRANGER = as_user("stranger")
PROVIDER = as_user("payroll.provider")
