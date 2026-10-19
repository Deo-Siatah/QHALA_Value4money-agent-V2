import pytest
import psycopg
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from mcp_server import audit
from mcp_server.audit import audited, log_call, AuditError
from conftest import SharedConnection


class SubmitErrorStub(ValueError):
    """Stand-in for a tool's own error type."""


@pytest.fixture
def logged(audit_conn, monkeypatch):
    """Point audit at the temporary audit_log table and return a reader for its rows."""
    monkeypatch.setattr(audit, "get_conn", lambda: SharedConnection(audit_conn))

    def rows():
        return audit_conn.execute(
            "SELECT * FROM audit_log WHERE run_id = %s ORDER BY id", ("run-1",)).fetchall()

    return rows


def test_log_call_writes_one_row(logged):
    row_id = log_call("run-1", "benchmark_price", {"bids": [{"bidder": "Apex", "total": 5.0}]},
                      {"n": 6, "confidence": "high"})

    rows = logged()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == row_id
    assert row["run_id"] == "run-1"
    assert row["actor"] == "agent"
    assert row["tool"] == "benchmark_price"
    assert row["input"] == {"bids": [{"bidder": "Apex", "total": 5.0}]}
    assert row["output"] == {"n": 6, "confidence": "high"}
    assert row["approved_by"] is None
    assert row["ts"] is not None


def test_non_json_values_are_coerced(logged):
    file_path = Path("outputs/reports/run-1.md")
    log_call("run-1", "submit_for_committee",
             {"when": datetime(2026, 3, 1, 12, 30, tzinfo=timezone.utc),
              "amount": Decimal("4800000.50"), "file": file_path},
             {"ids": {"b", "a"}}, approved_by="Grace Wanjiku")

    row = logged()[0]
    assert row["input"] == {"when": "2026-03-01T12:30:00+00:00", "amount": 4800000.50,
                            "file": str(file_path)}
    assert sorted(row["output"]["ids"]) == ["a", "b"]
    assert row["approved_by"] == "Grace Wanjiku"


def test_audited_records_input_and_output_and_returns_the_value(logged):
    @audited("benchmark_price")
    def tool(run_id: str, bids: list, pool: list) -> dict:
        return {"n": len(pool), "bids": bids}

    result = tool("run-1", [{"bidder": "Apex", "total": 5.0}], [{"award_id": "a1", "value_kes": 5.0}])

    assert result == {"n": 1, "bids": [{"bidder": "Apex", "total": 5.0}]}
    row = logged()[0]
    assert row["tool"] == "benchmark_price"
    assert row["input"] == {"bids": [{"bidder": "Apex", "total": 5.0}],
                            "pool": [{"award_id": "a1", "value_kes": 5.0}]}
    assert row["output"] == {"n": 1, "bids": [{"bidder": "Apex", "total": 5.0}]}


def test_audited_logs_the_error_then_reraises(logged):
    @audited("submit_for_committee")
    def tool(run_id: str, run: str) -> None:
        raise SubmitErrorStub(f"run_id '{run}' has already been filed")

    with pytest.raises(SubmitErrorStub):
        tool("run-1", "run-1")

    assert logged()[0]["output"] == {"error": "SubmitErrorStub: run_id 'run-1' has already been filed"}


def test_audited_passes_approved_by_through(logged):
    @audited("submit_for_committee")
    def tool(run_id: str, approved_by: str) -> dict:
        return {"report_id": 1}

    tool("run-1", "Grace Wanjiku")

    assert logged()[0]["approved_by"] == "Grace Wanjiku"


@pytest.mark.parametrize("run_id", ["", "   ", None, 7])
def test_missing_run_id_is_refused_before_the_tool_runs(logged, run_id):
    calls = []

    @audited("benchmark_price")
    def tool(run_id: str) -> dict:
        calls.append(run_id)
        return {}

    with pytest.raises(AuditError) as err:
        tool(run_id)

    assert "run_id" in str(err.value)
    assert calls == []
    assert logged() == []


def test_failed_audit_insert_blocks_the_tool(monkeypatch):
    calls = []

    @audited("submit_for_committee")
    def tool(run_id: str, report_md: str, approved_by: str) -> dict:
        calls.append(report_md)
        return {"report_id": 1}

    def boom():
        raise psycopg.OperationalError("audit_log is unavailable")

    monkeypatch.setattr(audit, "get_conn", boom)

    with pytest.raises(AuditError):
        tool("run-1", "# Report\n", "Grace Wanjiku")

    assert calls == []


def test_failed_audit_update_raises_even_though_the_tool_ran(logged, monkeypatch):
    calls = []
    original = psycopg.Connection.execute

    def patched(self, query, params=None, **kwargs):
        if query.lstrip().upper().startswith("UPDATE"):
            raise psycopg.OperationalError("connection lost")
        return original(self, query, params, **kwargs)

    @audited("benchmark_price")
    def tool(run_id: str) -> dict:
        calls.append(run_id)
        return {"n": 0}

    monkeypatch.setattr(psycopg.Connection, "execute", patched)

    with pytest.raises(AuditError) as err:
        tool("run-1")

    assert calls == ["run-1"]
    assert "audit" in str(err.value).lower()
    assert logged()[0]["output"] is None


def test_the_audit_sequence_is_reported_against_the_highest_row_written():
    """The duplicate-key crash of 15 Oct happened because the sequence fell behind the table."""
    from mcp_server import audit

    class Conn:
        def execute(self, sql, *args):
            text = sql.strip()
            if "last_value" in text:
                return Rows([{"last_value": 2}])
            return Rows([{"max_id": 28}])

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0]

    report = audit.sequence_health(Conn())

    assert report["last_value"] == 2
    assert report["max_id"] == 28
    assert report["in_step"] is False
    assert "setval" in report["fix"]


def test_an_empty_audit_table_is_in_step():
    from mcp_server import audit

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0]

    class Conn:
        def execute(self, sql, *args):
            return Rows([{"last_value": 1, "max_id": None}] if "last_value" in sql
                        else [{"max_id": None}])

    report = audit.sequence_health(Conn())

    assert report["in_step"] is True
    assert report["fix"] == ""


def test_a_sequence_ahead_of_the_table_is_still_in_step():
    """An unused sequence is harmless; only falling behind produces duplicate keys."""
    from mcp_server import audit

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def fetchone(self):
            return self._rows[0]

    class Conn:
        def execute(self, sql, *args):
            return Rows([{"last_value": 90}] if "last_value" in sql else [{"max_id": 28}])

    report = audit.sequence_health(Conn())

    assert report["in_step"] is True
