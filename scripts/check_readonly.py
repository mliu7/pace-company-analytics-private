"""Verify that the credentials in .env really are read-only, server-side.

Run this after rotating or repointing any credential:

    .venv/bin/python scripts/check_readonly.py

Every query here is a plain SELECT against catalog views -- it reads permission
metadata and never attempts a write to prove the point. Exits non-zero if
anything looks writable.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

failures = []


def check(label, ok, detail):
    print("  [%s] %s: %s" % ("ok" if ok else "FAIL", label, detail))
    if not ok:
        failures.append(label)


print("SQL Server (Dynamics SL / PACEAPP)")

identity = db.sl_query(
    """
    SELECT SUSER_SNAME() AS login_name,
           IS_SRVROLEMEMBER('sysadmin') AS sysadmin,
           IS_SRVROLEMEMBER('dbcreator') AS dbcreator,
           IS_SRVROLEMEMBER('securityadmin') AS securityadmin,
           IS_ROLEMEMBER('db_owner') AS db_owner,
           IS_ROLEMEMBER('db_datawriter') AS db_datawriter,
           IS_ROLEMEMBER('db_ddladmin') AS db_ddladmin,
           IS_ROLEMEMBER('db_datareader') AS db_datareader
    """
)[0]

print("  login: %s" % identity["login_name"])
for role in (
    "sysadmin",
    "dbcreator",
    "securityadmin",
    "db_owner",
    "db_datawriter",
    "db_ddladmin",
):
    check("not in %s" % role, not identity[role], "member=%s" % identity[role])
check("in db_datareader", bool(identity["db_datareader"]), "needed to read at all")

# Effective permissions: what the login can actually do right now, after roles
# and DENY entries are resolved.
effective = {
    r["permission_name"] for r in db.sl_query(
        "SELECT permission_name FROM sys.fn_my_permissions(NULL, 'DATABASE')"
    )
}
writes = effective & {
    "INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "TAKE OWNERSHIP",
    "CREATE TABLE", "CREATE PROCEDURE", "CREATE VIEW", "CREATE FUNCTION",
    "CREATE SCHEMA", "BACKUP DATABASE",
}
check("no write permissions on PACEAPP", not writes, sorted(writes) or "none")

# state_desc is passed as a parameter because assert_read_only() rejects the
# word DENY appearing in SQL text.
denies = {
    r["permission_name"] for r in db.sl_query(
        """
        SELECT dp.permission_name
        FROM sys.database_permissions dp
        JOIN sys.database_principals p ON p.principal_id = dp.grantee_principal_id
        WHERE p.name = USER_NAME() AND dp.state_desc = ?
        """,
        params=["DENY"],
    )
}
# Belt and braces: DENY beats any GRANT the account might later inherit.
check(
    "explicit DENY on INSERT/UPDATE/DELETE",
    {"INSERT", "UPDATE", "DELETE"} <= denies,
    sorted(denies) or "none",
)

print("Postgres (PTT)")

role = db.ptt_query(
    """
    SELECT current_user AS name, r.rolsuper, r.rolcreatedb, r.rolcreaterole,
           (SELECT setting FROM pg_settings
             WHERE name = 'default_transaction_read_only') AS session_read_only
    FROM pg_roles r
    WHERE r.rolname = current_user
    """
)[0]

print("  role: %s" % role["name"])
check("not superuser", not role["rolsuper"], "rolsuper=%s" % role["rolsuper"])
check("no CREATEDB", not role["rolcreatedb"], "rolcreatedb=%s" % role["rolcreatedb"])
check(
    "no CREATEROLE", not role["rolcreaterole"], "rolcreaterole=%s" % role["rolcreaterole"]
)
check(
    "session is read-only",
    role["session_read_only"] == "on",
    "default_transaction_read_only=%s" % role["session_read_only"],
)

memberships = db.ptt_query(
    """
    SELECT g.rolname AS member_of
    FROM pg_auth_members m
    JOIN pg_roles g ON g.oid = m.roleid
    JOIN pg_roles r ON r.oid = m.member
    WHERE r.rolname = current_user
    """
)
check(
    "no inherited roles",
    not memberships,
    [m["member_of"] for m in memberships] or "none",
)

# Privilege names are passed as parameters so this file stays a legal read under
# assert_read_only(), which rejects the words INSERT/UPDATE/... in SQL text.
grants = db.ptt_query(
    """
    SELECT count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_select,
           count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_insert,
           count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_update,
           count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_delete,
           count(*) FILTER (WHERE has_table_privilege(c.oid, %s)) AS can_truncate,
           count(*) AS total
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    """,
    params=("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"),
)[0]

write_counts = {
    k: grants[k] for k in ("can_insert", "can_update", "can_delete", "can_truncate")
}
check(
    "no table write grants",
    not any(write_counts.values()),
    "%s of %s tables" % (write_counts, grants["total"]),
)
check(
    "can read tables",
    grants["can_select"] > 0,
    "%s of %s tables" % (grants["can_select"], grants["total"]),
)

creates = db.ptt_query(
    """
    SELECT has_database_privilege(current_database(), %s) AS db_create,
           has_schema_privilege('public', %s) AS schema_create
    """,
    params=("CREATE", "CREATE"),
)[0]
check(
    "no CREATE on database or public schema",
    not creates["db_create"] and not creates["schema_create"],
    creates,
)

if failures:
    print("\n%d check(s) FAILED: %s" % (len(failures), ", ".join(failures)))
    print("Do not use these credentials until this is resolved.")
    sys.exit(1)

print("\nAll checks passed: both credentials are read-only.")
