"""Bulk upsert helpers on the local database (psycopg2 execute_values through Django's connection)."""

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from django.db import connection
from psycopg2.extras import execute_values


def content_hash(*parts):
    def norm(v):
        if isinstance(v, Decimal):
            return format(v.normalize(), "f")
        if isinstance(v, float):
            return format(Decimal(str(v)).normalize(), "f")
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if v is None:
            return ""
        return str(v)
    return hashlib.sha256("|".join(norm(p) for p in parts).encode("utf-8")).hexdigest()


def upsert(table, columns, rows, conflict_cols, update_cols=None, page_size=2000, returning=None):
    """INSERT ... ON CONFLICT (conflict_cols) DO UPDATE SET update_cols (or DO NOTHING when update_cols is None).

    Returns number of rows affected (inserted or updated)."""
    if not rows:
        return 0
    # dedupe on the conflict key (keep the last occurrence) — a single statement may not touch a row twice
    key_idx = [columns.index(c) for c in conflict_cols]
    seen = {}
    for r in rows:
        seen[tuple(r[i] for i in key_idx)] = r
    if len(seen) != len(rows):
        rows = list(seen.values())
    cols_sql = ", ".join('"%s"' % c for c in columns)
    conflict_sql = ", ".join('"%s"' % c for c in conflict_cols)
    if update_cols:
        set_sql = ", ".join('"%s" = EXCLUDED."%s"' % (c, c) for c in update_cols)
        # only update when something changed
        content_cols = [c for c in update_cols if not c.endswith("_run_id") and c not in ("updated_at", "ingestion_run_id")] or update_cols
        distinct_sql = " OR ".join('"%s"."%s" IS DISTINCT FROM EXCLUDED."%s"' % (table, c, c) for c in content_cols)
        action = "DO UPDATE SET %s WHERE %s" % (set_sql, distinct_sql)
    else:
        action = "DO NOTHING"
    sql = 'INSERT INTO "%s" (%s) VALUES %%s ON CONFLICT (%s) %s' % (table, cols_sql, conflict_sql, action)
    if returning:
        sql += " RETURNING %s" % returning
    total = 0
    with connection.cursor() as cur:
        raw = cur.cursor
        for i in range(0, len(rows), page_size):
            chunk = rows[i:i + page_size]
            if returning:
                out = execute_values(raw, sql, chunk, page_size=page_size, fetch=True)
                total += len(out)
            else:
                execute_values(raw, sql, chunk, page_size=page_size)
                total += raw.rowcount if raw.rowcount and raw.rowcount > 0 else 0
    return total


_COLTYPES = {}


def column_types(table):
    if table not in _COLTYPES:
        with connection.cursor() as cur:
            cur.execute("SELECT column_name, data_type, udt_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", [table])
            types = {}
            for col, dtype, udt in cur.fetchall():
                types[col] = {"character varying": "varchar", "timestamp with time zone": "timestamptz", "double precision": "float8"}.get(dtype, udt if dtype == "USER-DEFINED" or dtype == "ARRAY" else dtype)
            _COLTYPES[table] = types
    return _COLTYPES[table]


def bulk_update_from_values(table, key_col, columns, rows, page_size=2000):
    """UPDATE table SET col = v.col::type FROM (VALUES ...) v(key, cols...) WHERE table.key = v.key."""
    if not rows:
        return 0
    types = column_types(table)
    set_sql = ", ".join('"%s" = v."%s"::%s' % (c, c, types.get(c, "text")) for c in columns)
    vcols = ", ".join('"%s"' % c for c in [key_col] + list(columns))
    sql = 'UPDATE "%s" AS t SET %s FROM (VALUES %%s) AS v(%s) WHERE t."%s" = v."%s"::%s' % (table, set_sql, vcols, key_col, key_col, types.get(key_col, "bigint"))
    total = 0
    with connection.cursor() as cur:
        raw = cur.cursor
        for i in range(0, len(rows), page_size):
            execute_values(raw, sql, rows[i:i + page_size], page_size=page_size)
            total += raw.rowcount or 0
    return total


def fetch_dict(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    return str(o)


def dumps(o):
    return json.dumps(o, default=json_default)
