#!/usr/bin/env python
"""Ad-hoc READ-ONLY query runner for the three databases this project touches.

    .venv/bin/python scripts/query.py sl    "SELECT TOP 5 project, project_desc FROM PJPROJ WHERE gl_subacct='0700'"
    .venv/bin/python scripts/query.py ptt   "SELECT id, project_id, description FROM project_project WHERE client_id=7 LIMIT 5"
    .venv/bin/python scripts/query.py local "SELECT display_number, actual_gp_percent FROM core_project ORDER BY billed_revenue DESC NULLS LAST LIMIT 5"

sl / ptt go through db.py (SELECT-only guard, transaction always rolled back, READ UNCOMMITTED on SL).
local is the app's own PostgreSQL (pace_company_analytics on 127.0.0.1:5433) — also opened read-only here.
Optional third argument = max rows (default 200). Prints a compact table.
"""

import datetime
import decimal
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db  # noqa: E402  (the repo's read-only helper)


def local_query(sql, max_rows=None):
    import psycopg2
    import psycopg2.extras
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
    env = lambda k, d="": (os.environ.get(k) or d).strip().strip('"')
    db.assert_read_only(sql)
    conn = psycopg2.connect(host=env("PCA_DB_HOST", "127.0.0.1"), port=env("PCA_DB_PORT", "5433"), dbname=env("PCA_DB_NAME", "pace_company_analytics"),
                            user=env("PCA_DB_USER", os.environ.get("USER", "")), password=env("PCA_DB_PASSWORD", ""))
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.rollback()
        conn.close()


def fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, str):
        v = v.rstrip()
        return v if len(v) <= 60 else v[:57] + "..."
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    if isinstance(v, datetime.datetime):
        return v.isoformat(sep=" ")[:19]
    if isinstance(v, datetime.date):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return "<bytes>"
    return str(v)


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ("sl", "ptt", "local"):
        print(__doc__)
        sys.exit(2)
    which, sql = sys.argv[1], sys.argv[2]
    max_rows = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    fn = {"sl": db.sl_query, "ptt": db.ptt_query, "local": local_query}[which]
    rows = fn(sql, max_rows=max_rows)
    if not rows:
        print("(0 rows)")
        return
    cols = list(rows[0].keys())
    table = [[fmt(r[c]) for c in cols] for r in rows]
    widths = [max(len(c), *(len(t[i]) for t in table)) for i, c in enumerate(cols)]
    print(" | ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
    print("-+-".join("-" * w for w in widths))
    for t in table:
        print(" | ".join(t[i].ljust(widths[i]) for i in range(len(cols))))
    print("(%d rows%s)" % (len(rows), ", truncated" if len(rows) >= max_rows else ""))


if __name__ == "__main__":
    main()
