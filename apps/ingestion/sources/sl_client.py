"""Dynamics SL (SQL Server 2017) read-only client (spec v3 §2.3).

pyodbc + ODBC Driver 18, READ UNCOMMITTED, never commits. Only PACEAPP.
"""

import logging
from contextlib import contextmanager

import pyodbc
from django.conf import settings

from .guard import load_query, redact

log = logging.getLogger(__name__)

EXPECTED_ROLE = "db_datareader"
EXPECTED_EFFECTIVE = {"CONNECT", "SELECT", "VIEW ANY COLUMN ENCRYPTION KEY DEFINITION", "VIEW ANY COLUMN MASTER KEY DEFINITION"}
FORBIDDEN_EFFECTIVE = {"INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "TAKE OWNERSHIP", "CREATE TABLE", "CREATE PROCEDURE",
                       "CREATE VIEW", "CREATE FUNCTION", "CREATE SCHEMA", "BACKUP DATABASE", "EXECUTE"}


@contextmanager
def sl_connection():
    cfg = settings.SL_SOURCE
    if cfg["database"].upper() != "PACEAPP":
        raise RuntimeError("SL client refuses any database other than PACEAPP (got %s)" % cfg["database"])
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};SERVER=%s,%s;DATABASE=%s;UID=%s;PWD=%s;"
        "Encrypt=no;TrustServerCertificate=yes;APP=PaceCompanyAnalytics;ApplicationIntent=ReadOnly;"
        % (cfg["server"], cfg["port"], cfg["database"], cfg["user"], cfg["password"])
    )
    conn = pyodbc.connect(conn_str, timeout=30)
    try:
        conn.autocommit = False
        conn.timeout = 600
        conn.setdecoding(pyodbc.SQL_CHAR, encoding="latin-1")
        cur = conn.cursor()
        cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
        yield conn
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()


def _rows(cursor):
    columns = [d[0] for d in cursor.description]
    while True:
        batch = cursor.fetchmany(5000)
        if not batch:
            break
        for r in batch:
            yield dict(zip(columns, r))


def fetch_all(query_name, params=None):
    sql, _ = load_query(query_name)
    with sl_connection() as conn:
        cur = conn.cursor()
        cur.arraysize = 5000
        cur.execute(sql, params or [])
        return list(_rows(cur))


def iter_rows(query_name, params=None):
    sql, _ = load_query(query_name)
    with sl_connection() as conn:
        cur = conn.cursor()
        cur.arraysize = 5000
        cur.execute(sql, params or [])
        for r in _rows(cur):
            yield r


def permissions_audit():
    rows = fetch_all("sl.permissions_audit", ["DENY"])
    roles = {r["item"].strip() for r in rows if r["kind"] == "role"}
    effective = {r["item"].strip() for r in rows if r["kind"] == "effective"}
    denies = {r["item"].strip() for r in rows if r["kind"] == "explicit"}
    ok = (roles == {EXPECTED_ROLE} and not (effective & FORBIDDEN_EFFECTIVE) and {"INSERT", "UPDATE", "DELETE"} <= denies)
    result = {"roles": sorted(roles), "effective": sorted(effective), "denies": sorted(denies), "ok": ok}
    if not ok:
        raise RuntimeError("SL login is not the expected read-only shape: %s" % redact(result))
    return result
