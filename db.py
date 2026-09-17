"""Read-only database access for the performance lab.

Both databases here are LIVE PRODUCTION systems. The credentials in .env are
read-only accounts, verified server-side on 2026-08-17:

  - SQL Server (Dynamics SL, PACEAPP): login `sl_reader`. Member of
    db_datareader and nothing else; explicit DENY on INSERT, UPDATE and DELETE
    at the database level (DENY outranks any GRANT, including one inherited from
    a role). Effective database permissions are only CONNECT, SELECT and two
    VIEW ANY ... KEY DEFINITION. At the server level it holds only CONNECT SQL
    and VIEW ANY DATABASE -- not sysadmin, dbcreator or securityadmin.

  - Postgres (PTT): role `ptt_reader` on the `docker` database (PostgreSQL 9.4).
    Not a superuser, no CREATEDB/CREATEROLE, no role memberships. It has SELECT
    on all user tables and INSERT/UPDATE/DELETE/TRUNCATE on none of them, and no
    CREATE on the database or the public schema. Connections additionally set
    default_transaction_read_only=on, so the server rejects writes as well.

The client-side guards below (statement allowlist, rollback-always) are a second
layer, not the only thing standing between us and a write. Keep them anyway: an
account's grants can change without this file changing.

Re-verify with scripts/check_readonly.py if the credentials in .env are ever
rotated or repointed.

Use sl_query() / ptt_query(). Do not build ad-hoc connections elsewhere.
"""

import os
import re

from dotenv import load_dotenv

_ENV_LOADED = False


def _env(key):
    """Read a value from .env, tolerating the ` = "quoted"` style used there."""
    global _ENV_LOADED
    if not _ENV_LOADED:
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        _ENV_LOADED = True
    value = os.environ.get(key)
    if value is None:
        raise KeyError("%s is not set in .env" % key)
    return value.strip().strip('"').strip("'")


class WriteAttemptError(Exception):
    """Raised when a statement is not a plain read."""


# Anything that could modify data, schema, server state, or reach outside the
# query. Matched as whole words anywhere in the statement.
_FORBIDDEN = re.compile(
    r"\b("
    r"insert|update|delete|merge|truncate|drop|create|alter|grant|revoke|deny|"
    r"exec|execute|backup|restore|shutdown|reconfigure|checkpoint|dbcc|"
    r"sp_\w+|xp_\w+|into|openrowset|openquery|bulk"
    r")\b",
    re.IGNORECASE,
)

_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.DOTALL)


def assert_read_only(sql):
    """Reject anything that is not a single SELECT-shaped statement.

    Deliberately strict: it is easier to loosen this for a query you have read
    than to explain a write to a production Dynamics SL table. `SELECT ... INTO`
    is blocked along with the rest, since it creates a table.
    """
    stripped = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()

    if not stripped:
        raise WriteAttemptError("empty statement")

    if ";" in stripped:
        raise WriteAttemptError(
            "multiple statements in one call; send them one at a time"
        )

    if not re.match(r"^(select|with)\b", stripped, re.IGNORECASE):
        raise WriteAttemptError(
            "statement must start with SELECT or WITH, got: %.60s" % stripped
        )

    found = _FORBIDDEN.search(stripped)
    if found:
        raise WriteAttemptError(
            "forbidden keyword %r in a read-only query" % found.group(0)
        )

    return stripped


def sl_query(sql, params=None, max_rows=None):
    """Run a read-only query against Dynamics SL (SQL Server) and return rows.

    Rows come back as a list of dicts. The login itself cannot write (see the
    module docstring). On top of that, the connection is opened per call with
    autocommit off, rolled back, and closed -- so even a statement that somehow
    got past assert_read_only() would not be committed.

    READ UNCOMMITTED is set because PACEAPP is the live SL database and we must
    not take shared locks that block users. It means dirty reads are possible;
    for financial reporting, re-run anything that looks off.
    """
    import pyodbc

    statement = assert_read_only(sql)

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=%s,%s;DATABASE=%s;UID=%s;PWD=%s;"
        "Encrypt=no;TrustServerCertificate=yes;"
        % (
            _env("SQL_SERVER_IP"),
            _env("SQL_SERVER_PORT"),
            _env("SQL_SERVER_DATABASE"),
            _env("SQL_SERVER_USERNAME"),
            _env("SQL_SERVER_PASSWORD"),
        )
    )

    conn = pyodbc.connect(conn_str, timeout=30)
    try:
        conn.autocommit = False
        cursor = conn.cursor()
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        cursor.execute(statement, params or [])
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchmany(max_rows) if max_rows else cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        # Never commit. Any accidental write dies here.
        conn.rollback()
        conn.close()


def ptt_query(sql, params=None, max_rows=None):
    """Run a read-only query against the PTT Postgres database.

    Two server-side guarantees here: the `ptt_reader` role holds no write grants on
    any table, and default_transaction_read_only=on makes Postgres reject writes
    for the session regardless. The statement allowlist is a third layer.
    """
    import psycopg2
    import psycopg2.extras

    statement = assert_read_only(sql)

    conn = psycopg2.connect(
        host=_env("PTT_SERVER_IP"),
        port=_env("PTT_SERVER_PORT"),
        dbname=_env("PTT_SERVER_DATABASE"),
        user=_env("PTT_SERVER_USERNAME"),
        password=_env("PTT_SERVER_PASSWORD"),
        options="-c default_transaction_read_only=on",
        connect_timeout=30,
    )
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(statement, params)
            rows = cursor.fetchmany(max_rows) if max_rows else cursor.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.rollback()
        conn.close()
