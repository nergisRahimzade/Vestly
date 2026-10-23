from pydantic import BaseModel, ConfigDict, Field


class LineIn(BaseModel):
    account_code: str
    debit: str
    credit: str
    department: str | None = None


class JournalIn(BaseModel):
    entity_code: str
    period: str
    memo: str = Field(min_length=1, max_length=500)
    booking_key: str | None = Field(default=None, max_length=160)
    lines: list[LineIn] = Field(min_length=2)


class ReviewIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class LineOut(BaseModel):
    id: str
    line_number: int
    account_code: str
    account_name: str
    debit: str
    credit: str
    department: str | None


class JournalOut(BaseModel):
    id: str
    number: str
    entity_code: str
    currency: str
    period: str
    memo: str
    booking_key: str | None
    status: str
    prepared_by: str
    reviewed_by: str | None
    review_note: str | None
    posted_by: str | None
    total_debit: str
    total_credit: str
    lines: list[LineOut]


class PeriodOut(BaseModel):
    entity_code: str
    period: str
    status: str
    locked_by: str | None
    reopened_by: str | None = None
    reopen_reason: str | None = None


class ReopenIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class EntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    currency: str


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    account_type: str
    normal_balance: str
