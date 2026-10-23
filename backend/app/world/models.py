from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.ledger.models import new_id


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)


class Person(Base):
    """An identifier seen in a source file. This is not a decision about who was employed."""

    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("employee_number", name="person_number_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    department: Mapped[str] = mapped_column(String(32), nullable=False)
    listed_in_hr: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_on: Mapped[str] = mapped_column(String(10), nullable=False)


class HrCensusRow(Base):
    __tablename__ = "hr_census_rows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    department: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    hire_date: Mapped[str] = mapped_column(String(10), nullable=False)
    termination_date: Mapped[str | None] = mapped_column(String(10))
    termination_recorded_on: Mapped[str | None] = mapped_column(String(10))
    annual_salary: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    bank_account: Mapped[str] = mapped_column(String(32), nullable=False)
    bank_routing: Mapped[str] = mapped_column(String(16), nullable=False)


class PayrollRegisterRow(Base):
    __tablename__ = "payroll_register_rows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    department: Mapped[str] = mapped_column(String(32), nullable=False)
    pay_period_start: Mapped[str] = mapped_column(String(10), nullable=False)
    pay_period_end: Mapped[str] = mapped_column(String(10), nullable=False)
    gross_wages: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    employee_withholdings: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    employer_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    employer_contribution: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(32), nullable=False)
    retro_effective_date: Mapped[str | None] = mapped_column(String(10))
    retro_gross: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))


class FundingInstruction(Base):
    __tablename__ = "funding_instructions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    cleared: Mapped[bool] = mapped_column(Boolean, nullable=False)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    grant_number: Mapped[str] = mapped_column(String(32), nullable=False)
    employee_number: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(120), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    department: Mapped[str] = mapped_column(String(32), nullable=False)
    grant_date: Mapped[str] = mapped_column(String(10), nullable=False)
    cliff_end: Mapped[str] = mapped_column(String(10), nullable=False)
    vest_end: Mapped[str] = mapped_column(String(10), nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    fair_value: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    board_approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    board_approval_date: Mapped[str | None] = mapped_column(String(10))
    platform_status: Mapped[str] = mapped_column(String(16), nullable=False)
    monthly_expense: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)


class BankLine(Base):
    __tablename__ = "bank_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    posted_on: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(48), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(32), nullable=False)


class LedgerExportLine(Base):
    __tablename__ = "ledger_export_lines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id"), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    entity_code: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_memo: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    account_code: Mapped[str] = mapped_column(String(8), nullable=False)
    department: Mapped[str | None] = mapped_column(String(32))
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)


class WorldLoad(Base):
    __tablename__ = "world_loads"

    version: Mapped[str] = mapped_column(String(16), primary_key=True)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Injected scenarios (kind, employee_number, period) as JSON; none for the world as shipped.
    scenarios: Mapped[str | None] = mapped_column(String)


class DropLoad(Base):
    """One month's file drop, as received. The rate card travels with it, so nothing reads fixture files later."""

    __tablename__ = "drop_loads"

    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_on: Mapped[str] = mapped_column(String(10), nullable=False)
    closed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rate_card: Mapped[str] = mapped_column(String, nullable=False)
    loaded_by: Mapped[str] = mapped_column(String(48), nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
