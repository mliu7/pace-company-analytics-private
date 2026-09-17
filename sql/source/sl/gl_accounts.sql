-- sl.gl_accounts — chart of accounts, for labelling PJTran.gl_acct (freight, union funds, payroll tax).
SELECT RTRIM(a.Acct) AS gl_account, RTRIM(a.Descr) AS description, RTRIM(a.AcctType) AS acct_type, RTRIM(a.Active) AS active
FROM Account a
ORDER BY a.Acct
