"""PTT (PostgreSQL 9.4) read-only client (spec v3 §2.3).

Only this module and sl_client.py may open source connections. Every
transaction is READ ONLY and rolled back; large reads stream through a
server-side cursor.
"""

import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
from django.conf import settings

from .guard import load_query, redact

log = logging.getLogger(__name__)


@contextmanager
def ptt_connection():
    cfg = settings.PTT_SOURCE
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], dbname=cfg["dbname"], user=cfg["user"], password=cfg["password"],
        options="-c default_transaction_read_only=on -c statement_timeout=600000 -c lock_timeout=10000 -c application_name=pace_company_analytics",
        connect_timeout=30,
    )
    try:
        conn.set_session(readonly=True, autocommit=False)
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def _params(params):
    p = {"client_id": settings.PTT_SOURCE["client_id"]}
    p.update(params or {})
    return p


def fetch_all(query_name, params=None):
    sql, _ = load_query(query_name)
    with ptt_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, _params(params))
            return [dict(r) for r in cur.fetchall()]


def iter_rows(query_name, params=None, chunk_size=5000):
    """Stream rows through a named (server-side) cursor."""
    sql, _ = load_query(query_name)
    with ptt_connection() as conn:
        with conn.cursor(name="pca_%s" % query_name.replace(".", "_"), cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.itersize = chunk_size
            cur.execute(sql, _params(params))
            for row in cur:
                yield dict(row)


def permissions_audit():
    """Catalog-only proof that the role cannot write; raises if it can."""
    with ptt_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT current_user AS name, r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
                "(SELECT setting FROM pg_settings WHERE name='default_transaction_read_only') AS ro "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
            role = dict(cur.fetchone())
            cur.execute(
                "SELECT count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_insert, "
                "count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_update, "
                "count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_delete, "
                "count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_truncate, count(*) AS total "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')",
                ("INSERT", "UPDATE", "DELETE", "TRUNCATE"),
            )
            grants = dict(cur.fetchone())
    ok = (not role["rolsuper"] and not role["rolcreatedb"] and not role["rolcreaterole"] and role["ro"] == "on"
          and not any(grants[k] for k in ("can_insert", "can_update", "can_delete", "can_truncate")))
    result = {"role": role, "grants": grants, "ok": ok}
    if not ok:
        raise RuntimeError("PTT role is not read-only: %s" % redact(result))
    return result
