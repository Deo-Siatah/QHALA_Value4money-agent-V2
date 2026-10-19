# Give the run a memory that outlives the process, so an approval can pause now and resume later.
#
# . LangGraph writes the whole run state to Postgres
# every time the graph pauses, and reads it back when the run is resumed, so the approval interrupt
# in agent/nodes/approval.py survives the process ending:
#
# The tables are LangGraph's own, created on first use by an idempotent setup, and they never touch
# the tender tables. The sync saver is the right one here because every tool and every node in this
# project is synchronous; the async saver is never imported.
#
# Usage:
#     with checkpointer() as saver:
#         graph = build_graph(saver)
#         graph.invoke(state, {"configurable": {"thread_id": run_id}})
#         ... later, in any process ...
#         graph.invoke(Command(resume=decision), {"configurable": {"thread_id": run_id}})

# The migrations build indexes concurrently, which Postgres will not run inside a transaction, so
# setup gets its own autocommit connection. The saver itself is given a small pool rather than one
# bare connection: handed a single connection, psycopg keeps every checkpoint write in an uncommitted
# pipeline, and closing that connection throws the run's state away. The pool commits each write,
# which is what makes a pause survive the process.

from contextlib import contextmanager
from typing import Iterator, Optional

import psycopg
from psycopg_pool import ConnectionPool
from langgraph.checkpoint.postgres import PostgresSaver

from config import settings

def init_checkpoints(url: str) -> None:
    """Create LangGraph's checkpoint tables if they are missing. Safe to run every time."""
    conn = psycopg.connect(url, autocommit=True)
    try:
        PostgresSaver(conn).setup()
    finally:
        conn.close()


def _url(conn_string: Optional[str]) -> str:
    """The database this run keeps its memory in, refusing plainly when there is none."""
    url = conn_string if conn_string is not None else settings.database_url
    if not url:
        raise ValueError("DATABASE_URL is not set: a run needs a checkpointer to remember where it paused")
    return url


@contextmanager
def checkpointer(conn_string: Optional[str] = None) -> Iterator[PostgresSaver]:
    """Yield a PostgresSaver backed by a small pool, setting its tables up and closing it after."""
    url = _url(conn_string)
    init_checkpoints(url)
    pool = ConnectionPool(url, min_size=1, max_size=settings.db_pool_size, open=True)
    try:
        yield PostgresSaver(pool)
    finally:
        pool.close()


_shared_saver: Optional[PostgresSaver] = None
_shared_pool: Optional[ConnectionPool] = None


def shared_saver(conn_string: Optional[str] = None) -> PostgresSaver:
    """The one saver this process keeps, opened on first use and closed by close_shared_saver().

    The scripted runners above open a saver for the length of one command, which is right for a
    process that ends at the approval gate. A server does not end there: it holds runs that are
    waiting for a person, and each of those is resumed by a later HTTP request. Opening a pool per
    request would set the tables up again every time and hand the pool back while a paused run still
    needed it. The connection string is read on the first call only, because a run paused under one
    database must not be resumed against another.
    """
    global _shared_saver, _shared_pool
    if _shared_saver is not None:
        return _shared_saver
    url = _url(conn_string)
    init_checkpoints(url)
    _shared_pool = ConnectionPool(url, min_size=1, max_size=settings.db_pool_size, open=True)
    _shared_saver = PostgresSaver(_shared_pool)
    return _shared_saver


def close_shared_saver() -> None:
    """Close the process-wide saver's pool. Harmless when nothing was ever opened.

    Only the pool is closed. PostgresSaver wraps it and has no close of its own in this version, and
    the pool is what holds the connections a checkpointer would otherwise leak.
    """
    global _shared_saver, _shared_pool
    _shared_saver = None
    pool, _shared_pool = _shared_pool, None
    if pool is not None:
        pool.close()
