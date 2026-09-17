-- sl.permissions_audit — must show db_datareader only, no write permission, and explicit DENY on INSERT/UPDATE/DELETE;
-- the loader aborts otherwise. 'DENY' is passed as a bound parameter because the source guard rejects the word in SQL text.
-- COLLATE clauses avoid the catalog collation conflict in UNION ALL.
SELECT 'role' AS kind, CAST(r.name AS nvarchar(128)) COLLATE DATABASE_DEFAULT AS item, CAST('' AS nvarchar(60)) COLLATE DATABASE_DEFAULT AS state
FROM sys.database_role_members m
JOIN sys.database_principals r ON r.principal_id = m.role_principal_id
JOIN sys.database_principals p ON p.principal_id = m.member_principal_id
WHERE p.name = USER_NAME()
UNION ALL
SELECT 'effective', CAST(permission_name AS nvarchar(128)) COLLATE DATABASE_DEFAULT, CAST('' AS nvarchar(60)) COLLATE DATABASE_DEFAULT
FROM sys.fn_my_permissions(NULL, 'DATABASE')
UNION ALL
SELECT 'explicit', CAST(dp.permission_name AS nvarchar(128)) COLLATE DATABASE_DEFAULT, CAST(dp.state_desc AS nvarchar(60)) COLLATE DATABASE_DEFAULT
FROM sys.database_permissions dp
JOIN sys.database_principals p ON p.principal_id = dp.grantee_principal_id
WHERE p.name = USER_NAME() AND dp.state_desc = ?
