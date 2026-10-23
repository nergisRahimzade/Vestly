from sqlalchemy import text

SQLITE_GUARDS = [
    """
    CREATE TRIGGER IF NOT EXISTS journal_period_must_be_open
    BEFORE INSERT ON journal_entries
    FOR EACH ROW
    WHEN (SELECT status FROM periods WHERE id = NEW.period_id) NOT IN ('open', 'reopened')
    BEGIN
      SELECT RAISE(ABORT, 'period_locked');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_review_needs_open_period
    BEFORE UPDATE OF status ON journal_entries
    FOR EACH ROW
    WHEN NEW.status IN ('approved', 'posted')
      AND (SELECT status FROM periods WHERE id = NEW.period_id) NOT IN ('open', 'reopened')
    BEGIN
      SELECT RAISE(ABORT, 'period_locked');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_moves_forward_only
    BEFORE UPDATE ON journal_entries
    FOR EACH ROW
    WHEN NOT (
      (OLD.status = 'draft' AND NEW.status IN ('draft', 'approved', 'rejected', 'withdrawn'))
      OR (OLD.status = 'approved' AND NEW.status = 'posted')
    )
    BEGIN
      SELECT RAISE(ABORT, 'journal_is_final');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS journal_must_balance_to_move
    BEFORE UPDATE OF status ON journal_entries
    FOR EACH ROW
    WHEN NEW.status IN ('approved', 'posted')
      AND (
        (SELECT COUNT(*) FROM journal_lines WHERE entry_id = NEW.id) < 2
        OR (SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE entry_id = NEW.id)
           <> (SELECT COALESCE(SUM(credit), 0) FROM journal_lines WHERE entry_id = NEW.id)
        OR (SELECT COALESCE(SUM(debit), 0) FROM journal_lines WHERE entry_id = NEW.id) <= 0
      )
    BEGIN
      SELECT RAISE(ABORT, 'unbalanced_journal');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS posted_journal_cannot_be_deleted
    BEFORE DELETE ON journal_entries
    FOR EACH ROW
    WHEN OLD.status IN ('posted', 'rejected', 'withdrawn')
    BEGIN
      SELECT RAISE(ABORT, 'journal_is_final');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS posted_lines_cannot_change
    BEFORE UPDATE ON journal_lines
    FOR EACH ROW
    WHEN (SELECT status FROM journal_entries WHERE id = OLD.entry_id) IN ('approved', 'posted', 'rejected', 'withdrawn')
    BEGIN
      SELECT RAISE(ABORT, 'journal_is_final');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS posted_lines_cannot_be_deleted
    BEFORE DELETE ON journal_lines
    FOR EACH ROW
    WHEN (SELECT status FROM journal_entries WHERE id = OLD.entry_id) IN ('approved', 'posted', 'rejected', 'withdrawn')
    BEGIN
      SELECT RAISE(ABORT, 'journal_is_final');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS reconciliation_runs_append_only_update
    BEFORE UPDATE ON reconciliation_runs
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS reconciliation_runs_append_only_delete
    BEFORE DELETE ON reconciliation_runs
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS reconciliations_append_only_update
    BEFORE UPDATE ON reconciliations
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS reconciliations_append_only_delete
    BEFORE DELETE ON reconciliations
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS equity_schedule_runs_append_only_update
    BEFORE UPDATE ON equity_schedule_runs
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS equity_schedule_runs_append_only_delete
    BEFORE DELETE ON equity_schedule_runs
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS equity_grant_months_append_only_update
    BEFORE UPDATE ON equity_grant_months
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS equity_grant_months_append_only_delete
    BEFORE DELETE ON equity_grant_months
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS evidence_events_append_only_update
    BEFORE UPDATE ON evidence_events
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS evidence_events_append_only_delete
    BEFORE DELETE ON evidence_events
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS precedents_append_only_update
    BEFORE UPDATE ON precedents
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS precedents_append_only_delete
    BEFORE DELETE ON precedents
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS source_documents_append_only_update
    BEFORE UPDATE ON source_documents
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS source_documents_append_only_delete
    BEFORE DELETE ON source_documents
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS approval_decisions_append_only_update
    BEFORE UPDATE ON approval_decisions
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS approval_decisions_append_only_delete
    BEFORE DELETE ON approval_decisions
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS callbacks_append_only_update
    BEFORE UPDATE ON callbacks
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS callbacks_append_only_delete
    BEFORE DELETE ON callbacks
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """,
]

POSTGRES_GUARDS = [
    """
    CREATE OR REPLACE FUNCTION vestly_reject_locked_period()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      period_status text;
    BEGIN
      SELECT status INTO period_status FROM periods WHERE id = NEW.period_id;
      IF period_status IS NULL OR period_status NOT IN ('open', 'reopened') THEN
        RAISE EXCEPTION 'period_locked' USING ERRCODE = '23514';
      END IF;
      RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS journal_period_must_be_open ON journal_entries
    """,
    """
    CREATE TRIGGER journal_period_must_be_open
    BEFORE INSERT ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION vestly_reject_locked_period()
    """,
    """
    CREATE OR REPLACE FUNCTION vestly_journal_must_balance()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      entry text;
      line_count int;
      total_debit numeric;
      total_credit numeric;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        entry := OLD.entry_id;
      ELSE
        entry := NEW.entry_id;
      END IF;
      SELECT COUNT(*), COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0)
        INTO line_count, total_debit, total_credit
      FROM journal_lines
      WHERE entry_id = entry;
      IF line_count < 2 OR total_debit <> total_credit OR total_debit <= 0 THEN
        RAISE EXCEPTION 'unbalanced_journal' USING ERRCODE = '23514';
      END IF;
      RETURN NULL;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS journal_lines_must_balance ON journal_lines
    """,
    """
    CREATE CONSTRAINT TRIGGER journal_lines_must_balance
    AFTER INSERT OR UPDATE OR DELETE ON journal_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION vestly_journal_must_balance()
    """,
    """
    CREATE OR REPLACE FUNCTION vestly_entry_must_balance()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      line_count int;
      total_debit numeric;
      total_credit numeric;
    BEGIN
      SELECT COUNT(*), COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0)
        INTO line_count, total_debit, total_credit
      FROM journal_lines
      WHERE entry_id = NEW.id;
      IF line_count < 2 OR total_debit <> total_credit OR total_debit <= 0 THEN
        RAISE EXCEPTION 'unbalanced_journal' USING ERRCODE = '23514';
      END IF;
      RETURN NULL;
    END;
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION vestly_journal_state_rules()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      period_status text;
    BEGIN
      IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('posted', 'rejected', 'withdrawn') THEN
          RAISE EXCEPTION 'journal_is_final' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
      END IF;
      IF NOT (
        (OLD.status = 'draft' AND NEW.status IN ('draft', 'approved', 'rejected', 'withdrawn'))
        OR (OLD.status = 'approved' AND NEW.status = 'posted')
      ) THEN
        RAISE EXCEPTION 'journal_is_final' USING ERRCODE = '23514';
      END IF;
      IF NEW.status IN ('approved', 'posted') THEN
        SELECT status INTO period_status FROM periods WHERE id = NEW.period_id;
        IF period_status IS NULL OR period_status NOT IN ('open', 'reopened') THEN
          RAISE EXCEPTION 'period_locked' USING ERRCODE = '23514';
        END IF;
      END IF;
      RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS journal_state_rules ON journal_entries
    """,
    """
    CREATE TRIGGER journal_state_rules
    BEFORE UPDATE OR DELETE ON journal_entries
    FOR EACH ROW
    EXECUTE FUNCTION vestly_journal_state_rules()
    """,
    """
    CREATE OR REPLACE FUNCTION vestly_final_lines()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
      entry_status text;
    BEGIN
      SELECT status INTO entry_status FROM journal_entries WHERE id = OLD.entry_id;
      IF entry_status IN ('approved', 'posted', 'rejected', 'withdrawn') THEN
        RAISE EXCEPTION 'journal_is_final' USING ERRCODE = '23514';
      END IF;
      IF TG_OP = 'DELETE' THEN
        RETURN OLD;
      END IF;
      RETURN NEW;
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS final_lines ON journal_lines
    """,
    """
    CREATE TRIGGER final_lines
    BEFORE UPDATE OR DELETE ON journal_lines
    FOR EACH ROW
    EXECUTE FUNCTION vestly_final_lines()
    """,
    """
    CREATE OR REPLACE FUNCTION vestly_append_only()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      RAISE EXCEPTION 'append_only' USING ERRCODE = '23514';
    END;
    $$
    """,
    """
    DROP TRIGGER IF EXISTS reconciliation_runs_append_only ON reconciliation_runs
    """,
    """
    CREATE TRIGGER reconciliation_runs_append_only
    BEFORE UPDATE OR DELETE ON reconciliation_runs
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS reconciliations_append_only ON reconciliations
    """,
    """
    CREATE TRIGGER reconciliations_append_only
    BEFORE UPDATE OR DELETE ON reconciliations
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS equity_schedule_runs_append_only ON equity_schedule_runs
    """,
    """
    CREATE TRIGGER equity_schedule_runs_append_only
    BEFORE UPDATE OR DELETE ON equity_schedule_runs
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS equity_grant_months_append_only ON equity_grant_months
    """,
    """
    CREATE TRIGGER equity_grant_months_append_only
    BEFORE UPDATE OR DELETE ON equity_grant_months
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS evidence_events_append_only ON evidence_events
    """,
    """
    CREATE TRIGGER evidence_events_append_only
    BEFORE UPDATE OR DELETE ON evidence_events
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS precedents_append_only ON precedents
    """,
    """
    CREATE TRIGGER precedents_append_only
    BEFORE UPDATE OR DELETE ON precedents
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS source_documents_append_only ON source_documents
    """,
    """
    CREATE TRIGGER source_documents_append_only
    BEFORE UPDATE OR DELETE ON source_documents
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS approval_decisions_append_only ON approval_decisions
    """,
    """
    CREATE TRIGGER approval_decisions_append_only
    BEFORE UPDATE OR DELETE ON approval_decisions
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS callbacks_append_only ON callbacks
    """,
    """
    CREATE TRIGGER callbacks_append_only
    BEFORE UPDATE OR DELETE ON callbacks
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """,
    """
    DROP TRIGGER IF EXISTS journal_entry_must_balance ON journal_entries
    """,
    """
    CREATE CONSTRAINT TRIGGER journal_entry_must_balance
    AFTER INSERT ON journal_entries
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION vestly_entry_must_balance()
    """,
]


# Cases and funding: the history of a case, people's answers, its links, and what the bank executed.
APPEND_ONLY_TABLES = ("case_transitions", "case_answers", "case_links", "bank_transfers", "error_log", "fx_rates", "observations")
for _table in APPEND_ONLY_TABLES:
    for _event in ("UPDATE", "DELETE"):
        SQLITE_GUARDS.append(
            f"""
    CREATE TRIGGER IF NOT EXISTS {_table}_append_only_{_event.lower()}
    BEFORE {_event} ON {_table}
    BEGIN
      SELECT RAISE(ABORT, 'append_only');
    END
    """
        )
    POSTGRES_GUARDS.append(f"DROP TRIGGER IF EXISTS {_table}_append_only ON {_table}")
    POSTGRES_GUARDS.append(
        f"""
    CREATE TRIGGER {_table}_append_only
    BEFORE UPDATE OR DELETE ON {_table}
    FOR EACH ROW
    EXECUTE FUNCTION vestly_append_only()
    """
    )


def install_guards(engine) -> None:
    statements = SQLITE_GUARDS if engine.dialect.name == "sqlite" else POSTGRES_GUARDS
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
