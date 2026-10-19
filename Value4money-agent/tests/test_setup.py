# These tests are the spec for loader/setup.py, the one command a stranger runs before anything
# else. It is the first thing they see, so the tests are about what it tells them: which database it
# connected to, what it created, whether it had to load the demo slice, and what it could not do.
# No database is needed: every test drives a fake connection that records the statements it is given.

import pytest

from loader import setup
from loader.setup import apply_demo_slice, apply_schema, main, sanity_lines


class FakeResult:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def fetchone(self):
        return {"count": self._value} if self._value is not None else (self._rows[0] if self._rows else None)

    def fetchall(self):
        return self._rows


class FakeConn:
    """A connection that answers counts and version queries, and records everything applied."""

    def __init__(self, awards=0, version="PostgreSQL 16.2"):
        self.awards = awards
        self.version = version
        self.applied = []

    def execute(self, sql, *args):
        text = sql.strip()
        if text.startswith("SELECT version()"):
            return FakeResult(rows=[{"version": self.version}])
        if "count(*)" in text and "FROM awards" in text:
            return FakeResult(value=self.awards)
        if text.startswith("--") or text.upper().startswith("CREATE") or text.startswith("INSERT"):
            self.applied.append(text[:60])
            return FakeResult()
        return FakeResult()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    @property
    def schema_statements(self):
        return [sql for sql in self.applied if sql.upper().startswith("CREATE")]



def test_it_says_which_database_it_connected_to(fake_console):
    """A stranger who typed the wrong URL should find out before anything is created."""
    lines = []

    setup.report_connection(FakeConn(), lines.append)

    assert any("PostgreSQL 16.2" in line for line in lines)


def test_it_applies_the_schema_before_the_indexes(fake_console):
    """The indexes need the pg_trgm extension the schema creates."""
    conn = FakeConn()

    apply_schema(conn, schema_sql="CREATE EXTENSION IF NOT EXISTS pg_trgm;",
                 indexes_sql="CREATE INDEX IF NOT EXISTS i ON awards (value_kes);")

    assert conn.applied[0].startswith("CREATE EXTENSION")
    assert conn.applied[1].startswith("CREATE INDEX")


def test_the_demo_slice_is_only_loaded_when_there_is_no_data(fake_console):
    """A stranger who already loaded the exports must not have the slice pushed at them."""
    empty, loaded = FakeConn(awards=0), FakeConn(awards=44369)
    lines = []

    apply_demo_slice(empty, slice_sql="INSERT INTO awards (award_id) VALUES ('x');",
                     emit=lines.append)
    lines_loaded = []
    apply_demo_slice(loaded, slice_sql="INSERT INTO awards (award_id) VALUES ('x');",
                     emit=lines_loaded.append)

    assert any("INSERT" in sql for sql in empty.applied)
    assert loaded.applied == []
    assert "already holds awards" in " ".join(lines_loaded)


def test_the_sanity_report_says_how_big_the_pool_is(fake_console):
    """The point of the slice is a visible benchmark pool, so the numbers are printed."""
    class PoolConn:
        """Answers the three shapes of query sanity_lines asks, with known figures."""

        def __init__(self):
            self.seen = []

        def execute(self, sql, *args):
            self.seen.append(sql)
            if sql.strip().startswith("SELECT version()"):
                return FakeResult(rows=[{"version": "PostgreSQL 18.6"}])
            if "percentile_cont" in sql:
                return FakeResult(rows=[{"median_kes": 2500000.0}])
            if "count(*) AS n" in sql:
                return FakeResult(rows=[{"n": 812}])
            return FakeResult(rows=[{"count": 3960}])

    conn = PoolConn()
    lines = []
    sanity_lines(conn, lines.append)

    text = " ".join(lines)
    assert "3960" in text
    assert "2,500,000" in text
    assert "fence" in text


def test_each_keyword_is_searched_as_a_substring_not_an_exact_word():
    """ILIKE 'fence' matches nothing; ILIKE '%fence%' matches every fence contract."""
    class Recording:
        def __init__(self):
            self.patterns = []

        def execute(self, sql, *args):
            if args:
                self.patterns.extend(args[0] if isinstance(args[0], tuple) else args)
            return FakeResult(rows=[{"median_kes": 1.0, "count": 1, "n": 1}])

    conn = Recording()
    sanity_lines(conn, lambda line="": None)

    assert len(conn.patterns) == 10
    for pattern in conn.patterns:
        assert pattern.startswith("%") and pattern.endswith("%")
        assert pattern.strip("%")


def test_the_median_is_computed_the_way_postgres_computes_it():
    """Postgres has no median() function: it is percentile_cont(0.5) WITHIN GROUP."""
    class Recorded:
        def __init__(self):
            self.seen = []

        def execute(self, sql, *args):
            self.seen.append(sql)
            return FakeResult(rows=[{"median_kes": 1.0, "count": 1, "n": 1}])

    conn = Recorded()
    sanity_lines(conn, lambda line="": None)

    assert any("percentile_cont(0.5) WITHIN GROUP (ORDER BY value_kes)" in sql for sql in conn.seen)


def test_a_missing_database_url_is_a_named_error_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(setup.settings, "database_url", "")

    code = main([])

    out = capsys.readouterr().out
    assert code != 0
    assert "DATABASE_URL" in out


def test_a_url_that_cannot_connect_is_a_named_error(monkeypatch, capsys):
    # connect_timeout keeps the refusal quick; without it the retry waits out the full timeout.
    monkeypatch.setattr(setup.settings, "database_url",
                        "postgresql://nobody@127.0.0.1:1/nothing?connect_timeout=1")

    code = main([])

    out = capsys.readouterr().out
    assert code != 0
    assert "connect" in out.lower()


def test_setup_refuses_a_database_that_is_not_postgres(monkeypatch, capsys):
    """MySQL would accept the URL and then fail three statements later with a syntax error."""
    class MySQLConn(FakeConn):
        def execute(self, sql, *args):
            if sql.strip().startswith("SELECT version()"):
                return FakeResult(rows=[{"version": "8.0.36"}])
            return super().execute(sql, *args)

    monkeypatch.setattr(setup, "connect", lambda url: MySQLConn())

    code = main([])

    assert code != 0
    assert "Postgres" in capsys.readouterr().out


@pytest.fixture
def fake_console(monkeypatch):
    """Setup prints through its own sink so tests read lines, not rich output."""
    monkeypatch.setattr(setup, "say", lambda line="": None)
    return True


def test_setup_warns_when_the_audit_sequence_would_fail_the_first_tool_call(capsys):
    """The drift is invisible until it throws, so setup says so while it can still be fixed."""
    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0]

    class DriftedConn(FakeConn):
        def execute(self, sql, *args):
            text = sql.strip()
            if "last_value" in text:
                return Rows([{"last_value": 2}])
            if "max(id)" in text:
                return Rows([{"max_id": 28}])
            return super().execute(sql, *args)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(setup.settings, "database_url", "postgresql://x")
    monkeypatch.setattr(setup, "connect", lambda url: DriftedConn())
    monkeypatch.setattr(setup, "sanity_lines", lambda conn, emit=None: None)
    try:
        code = main([])
    finally:
        monkeypatch.undo()

    out = capsys.readouterr().out
    assert code == 0
    assert "WARNING" in out
    assert "setval" in out


def test_setup_reports_a_sequence_that_is_in_step(capsys):
    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0]

    class HealthyConn(FakeConn):
        def execute(self, sql, *args):
            text = sql.strip()
            if "last_value" in text:
                return Rows([{"last_value": 40}])
            if "max(id)" in text:
                return Rows([{"max_id": 28}])
            return super().execute(sql, *args)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(setup.settings, "database_url", "postgresql://x")
    monkeypatch.setattr(setup, "connect", lambda url: HealthyConn())
    monkeypatch.setattr(setup, "sanity_lines", lambda conn, emit=None: None)
    try:
        main([])
    finally:
        monkeypatch.undo()

    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "audit_log: sequence at 40" in out
