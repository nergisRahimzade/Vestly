class LedgerError(Exception):
    """A refusal or a bad request. rule names the policy rule behind a refusal, when there is one."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        rule: str | None = None,
        actor: str | None = None,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.rule = rule
        self.actor = actor
        self.action = action
