-- AcctHist rows (every subaccount) for ONE GL account and fiscal year: beginning balance plus the
-- 13 period movements, natural sign (assets debit-positive). Book balance at the end of calendar
-- month m = BegBal + p00..p(m-1), summed over subaccounts (fiscal periods = calendar months).
-- Params: 1 = GL account, 2 = fiscal year 'YYYY'.
SELECT RTRIM(h.Acct)   AS acct,
       RTRIM(h.Sub)    AS sub,
       RTRIM(h.FiscYr) AS fisc_yr,
       h.BegBal        AS beg_bal,
       h.PtdBal00 AS p00, h.PtdBal01 AS p01, h.PtdBal02 AS p02, h.PtdBal03 AS p03,
       h.PtdBal04 AS p04, h.PtdBal05 AS p05, h.PtdBal06 AS p06, h.PtdBal07 AS p07,
       h.PtdBal08 AS p08, h.PtdBal09 AS p09, h.PtdBal10 AS p10, h.PtdBal11 AS p11, h.PtdBal12 AS p12
FROM dbo.AcctHist h
WHERE h.LedgerID = 'ACTUAL' AND h.Acct = ? AND h.FiscYr = ?
