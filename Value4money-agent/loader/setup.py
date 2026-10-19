# Bring an empty Postgres database up to the point where the agent can run, in one command.
#
#   uv run python -m loader.setup
#
# It does four things and prints what it did: connects and says which database that is, applies the
# schema and the indexes, loads the demo slice if there is no data yet, and reports how big the
# benchmark pool is. It does not load the full OCDS exports, and it never touches anything outside
# the database it was pointed at.
#
# Every step is safe to repeat: the schema and indexes are created only if missing, and the demo
# slice inserts only rows that are absent. Running it twice changes nothing the second time.

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from config import settings

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "sql" / "01_schema.sql"
INDEXES_PATH = ROOT / "sql" / "02_indexes.sql"
SLICE_PATH = ROOT / "sql" / "demo_data.sql"


class SetupError(RuntimeError):
    """Raised when setup cannot continue, carrying the one line a stranger needs."""


def say(line: str = "") -> None:
    """Print one line of progress, so the command says what it is doing as it does it."""
    print(line, flush=True)


def connect(url: str) -> Any:
    """A dict-row connection to the database the stranger pointed at."""
    import psycopg
    from psycopg.rows import dict_row

    try:
        return psycopg.connect(url, row_factory=dict_row)
    except Exception as exc:
        raise SetupError(f"could not connect to DATABASE_URL: {exc}") from exc


def read_sql(path: Path) -> str:
    """One SQL file as text, or a clear error naming the file."""
    if not path.exists():
        raise SetupError(f"missing SQL file: {path}. Clone the whole repository, not part of it.")
    return path.read_text(encoding="utf-8")


def report_connection(conn: Any, emit: Callable[[str], None] = say) -> None:
    """Say which database this is, because a wrong URL should be obvious from the first line."""
    version = conn.execute("SELECT version()").fetchone()["version"]
    if "postgres" not in version.lower():
        raise SetupError(f"this agent needs Postgres, and that database reports: {version}")
    emit(f"connected to {version.split(',')[0]}")


def apply_schema(conn: Any, schema_sql: str, indexes_sql: str,
                 emit: Callable[[str], None] = say) -> None:
    """Apply the tables first, then the indexes, which is the order they depend on."""
    emit("creating the schema")
    conn.execute(schema_sql)
    emit("creating the indexes")
    conn.execute(indexes_sql)
    conn.commit()


def award_count(conn: Any) -> int:
    """How many awards are in the database already."""
    return conn.execute("SELECT count(*) FROM awards").fetchone()["count"]


def apply_demo_slice(conn: Any, slice_sql: str,
                     emit: Callable[[str], None] = say) -> None:
    """Load the bundled demo data, but only into a database that has none."""
    if award_count(conn) > 0:
        emit("this database already holds awards, so the demo slice was not loaded")
        return
    emit("loading the demo slice, the tender and award records for fence, road, classroom and "
         "borehole work")
    conn.execute(slice_sql)
    conn.commit()
    emit(f"loaded {award_count(conn)} awards from sql/demo_data.sql")


def audit_health(conn: Any, emit: Callable[[str], None] = say) -> None:
    """Warn if audit_log's id sequence has fallen behind, which would fail the first tool call."""
    from mcp_server.audit import sequence_health

    report = sequence_health(conn)
    if not report.get("checked"):
        return
    if report["in_step"]:
        emit(f"audit_log: sequence at {report['last_value']}, highest row {report['max_id']}")
    else:
        emit(f"audit_log: WARNING its id sequence is at {report['last_value']} but row "
             f"{report['max_id']} exists, so the first tool call will fail on a duplicate key. Fix:")
        emit(f"  {report['fix']}")


def sanity_lines(conn: Any, emit: Callable[[str], None] = say) -> None:
    """Report the pool the agent will search, so it is visible before anything runs."""
    from loader.make_demo_slice import KEYWORDS

    total = conn.execute("SELECT count(*) FROM awards WHERE value_kes > 0").fetchone()["count"]
    emit(f"  awards with a value: {total}")
    median = conn.execute("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value_kes) "
                          "AS median_kes FROM awards WHERE value_kes > 0").fetchone()["median_kes"]
    if median is not None:
        emit(f"  median award value: KES {float(median):,.2f}")
    for keyword in KEYWORDS:
        pattern = "%" + keyword.strip("%") + "%"
        rows = conn.execute(
            "SELECT count(*) AS n FROM awards WHERE value_kes > 0 AND ("
            "award_title ILIKE %s OR ocid IN ("
            "SELECT ocid FROM tenders WHERE title ILIKE %s))", (pattern, pattern)).fetchone()
        emit(f"  {keyword.strip('%'):<10} {rows['n']} awards")


def main(argv: List[str] = None) -> int:
    """Run setup and report what it did. Returns 0 when the database is ready."""
    argv = sys.argv[1:] if argv is None else argv
    url = settings.database_url
    if not url:
        say("cannot start: DATABASE_URL is not set.")
        say("Copy .env.example to .env, put in the connection URL of a Postgres database you "
            "control, and run this again.")
        return 2

    try:
        conn = connect(url)
        try:
            report_connection(conn)
            apply_schema(conn, read_sql(SCHEMA_PATH), read_sql(INDEXES_PATH))
            apply_demo_slice(conn, read_sql(SLICE_PATH))
            audit_health(conn)
            say("")
            say("what the agent will search when you run it:")
            sanity_lines(conn)
            say("")
            say("ready. Run:  uv run python main.py")
        finally:
            conn.close()
    except SetupError as exc:
        say(f"cannot continue: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())