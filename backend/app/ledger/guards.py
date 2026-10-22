from sqlalchemy import text

SQLITE_GUARDS = [
    """
    CREATE TRIGGER IF NOT EXISTS journal_period_must_be_open
    BEFORE INSERT ON journal_entries
    FOR EACH ROW
    WHEN (SELECT status FROM periods WHERE id = NEW.period_id) IS NOT 'open'
    BEGIN
      SELECT RAISE(ABORT, 'period_locked');
    END
    """
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
      IF period_status IS DISTINCT FROM 'open' THEN
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


def install_guards(engine) -> None:
    statements = SQLITE_GUARDS if engine.dialect.name == "sqlite" else POSTGRES_GUARDS
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
