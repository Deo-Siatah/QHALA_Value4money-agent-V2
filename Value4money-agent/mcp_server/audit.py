# Write one row per tool call to audit_log: the inputs before, the output after.
# It never imports mcp, so agent/mcp_client.py can wrap direct tool calls the same way.
# The row is inserted before the tool runs, so a crash mid-call still leaves a trace.
# A failed audit write raises AuditError and blocks the tool: nothing runs unlogged.

import functools
import threading
import inspect
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import PurePath
from typing import Any, Callable, Dict, Optional
from psycopg.types.json import Jsonb
from config import settings
from .db import get_conn


class AuditError(RuntimeError):
    """Raised when a call could not be logged; the tool must not run."""


def _jsonable(value: Any) -> Any:
    """Turn a tool's value into something psycopg can store as JSONB."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


def _check_run_id(run_id: Any) -> str:
    """Refuse a missing run_id: audit_log.run_id is NOT NULL and is the trace key."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise AuditError(f"run_id must be a non-empty string, got {run_id!r}")
    return run_id


class _ConnectionPool:
    """A small pool of audit connections, built from get_conn so one code path opens them all.

    Tool calls can now run at the same time, and every audit write needs a connection of its own.
    Opening one per call exhausted what a free Postgres allows, and the run died with "server closed
    the connection unexpectedly" rather than anything naming the cause.
    """

    def __init__(self, size: int) -> None:
        self._size = max(1, size)
        self._idle: list = []
        self._lock = threading.Lock()

    @contextmanager
    def connection(self):
        """Hand out an idle connection, or open one, and give it back when the work is done."""
        with self._lock:
            conn = self._idle.pop() if self._idle else None
        owned = conn is None
        if conn is None:
            conn = get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                owned = True
                conn = None
            raise
        finally:
            if conn is not None and not owned:
                with self._lock:
                    if len(self._idle) < self._size:
                        self._idle.append(conn)
                        return
            if owned and conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Close every connection this pool is holding."""
        with self._lock:
            idle, self._idle = self._idle, []
        for conn in idle:
            try:
                conn.close()
            except Exception:
                pass


_pool_instance = None


def _pool() -> "_ConnectionPool":
    """One small connection pool for audit writes, built on first use."""
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = _ConnectionPool(settings.audit_pool_size)
    return _pool_instance


def close_pool() -> None:
    """Close the audit pool, so a process that is ending does not hold connections open."""
    global _pool_instance
    if _pool_instance is not None:
        _pool_instance.close()
        _pool_instance = None


@contextmanager
def _audit_conn():
    """A pooled connection for one audit statement, wrapped as AuditError."""
    try:
        pool = _pool()
    except Exception as exc:
        raise AuditError(f"audit_log write failed: {exc}") from exc
    try:
        with pool.connection() as conn:
            yield conn
    except AuditError:
        raise
    except Exception as exc:
        raise AuditError(f"audit_log write failed: {exc}") from exc


def sequence_health(conn: Any = None) -> dict:
    """Report whether the audit_log id sequence has fallen behind the rows already written.

    A reset that reinserts rows with their own  ids leaves the sequence where it was, and the next
    insert fails on the primary key with nothing in the message to say why. Reading it is cheap, so
    setup does it before the first run rather than during one.
    """
    owned = conn is None
    conn = conn or get_conn()
    try:
        try:
            row = conn.execute("SELECT last_value FROM audit_log_id_seq").fetchone()
            last_value = row["last_value"] if row else None
        except Exception:
            return {"last_value": None, "max_id": None, "in_step": True, "fix": "",
                    "checked": False}
        row = conn.execute("SELECT max(id) AS max_id FROM audit_log").fetchone()
        max_id = row["max_id"] if row else None
    finally:
        if owned:
            conn.close()

    in_step = last_value is not None and (max_id is None or last_value >= max_id)
    fix = "" if in_step else (
        f"SELECT setval('audit_log_id_seq', {max_id or 0}, true);")
    return {"last_value": last_value, "max_id": max_id, "in_step": in_step, "fix": fix,
            "checked": True}


def log_call(run_id: str, tool: str, inputs: Dict[str, Any], output: Optional[Any] = None,
             actor: str = "agent", approved_by: Optional[str] = None) -> int:
    """Insert one audit_log row and return its id."""
    _check_run_id(run_id)
    with _audit_conn() as conn:
        row = conn.execute(
            "INSERT INTO audit_log (run_id, actor, tool, input, output, approved_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (run_id, actor, tool, Jsonb(_jsonable(inputs)),
             None if output is None else Jsonb(_jsonable(output)), approved_by),
        ).fetchone()
    return row["id"]


def finish_call(row_id: int, output: Any) -> None:
    """Store the result of a call in the row inserted before it ran."""
    with _audit_conn() as conn:
        conn.execute("UPDATE audit_log SET output = %s WHERE id = %s", (Jsonb(_jsonable(output)), row_id))


def audited(tool: str, actor: str = "agent") -> Callable:
    """Wrap a tool function so every call is written to audit_log under its run_id."""
    def decorate(fn: Callable) -> Callable:
        signature = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            run_id = _check_run_id(bound.arguments.pop("run_id", None))
            approved_by = bound.arguments.get("approved_by")
            row_id = log_call(run_id, tool, bound.arguments, None, actor, approved_by)
            try:
                output = fn(*args, **kwargs)
            except Exception as exc:
                finish_call(row_id, {"error": f"{type(exc).__name__}: {exc}"})
                raise
            finish_call(row_id, output)
            return output

        wrapper.__signature__ = signature
        return wrapper

    return decorate
