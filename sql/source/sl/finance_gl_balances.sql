-- GL account balances by account x subaccount x fiscal year (AcctHist, ACTUAL ledger).
-- AcctHist stores NATURAL-SIGN balances: assets/expenses debit-positive, liabilities/equity/income
-- credit-positive (verified against GLTran DrAmt-CrAmt 2026-08-26). Fiscal periods = calendar months.
-- Account types: 1A asset, 2L liability+equity (equity = accounts 3xxxx, 39999 = system-maintained
-- YTD net income), 3I income, 4E expense. Param 1: earliest fiscal year to pull (e.g. '2024').
SELECT RTRIM(h.Acct)     AS acct,
       RTRIM(h.Sub)      AS sub,
       RTRIM(h.FiscYr)   AS fiscal_year,
       RTRIM(a.AcctType) AS acct_type,
       RTRIM(a.Descr)    AS descr,
       h.BegBal          AS beg_bal,
       h.PtdBal00 AS p00, h.PtdBal01 AS p01, h.PtdBal02 AS p02, h.PtdBal03 AS p03,
       h.PtdBal04 AS p04, h.PtdBal05 AS p05, h.PtdBal06 AS p06, h.PtdBal07 AS p07,
       h.PtdBal08 AS p08, h.PtdBal09 AS p09, h.PtdBal10 AS p10, h.PtdBal11 AS p11,
       h.PtdBal12 AS p12,
       h.LUpd_DateTime   AS sl_updated_at
FROM dbo.AcctHist h
JOIN dbo.Account a ON a.Acct = h.Acct
WHERE h.LedgerID = 'ACTUAL' AND h.FiscYr >= ?
