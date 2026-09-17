-- sl.gl_010_pnl — P&L truth for the 010 hardware business: AcctHist per-period balances for subaccounts
-- 0100 and 0101, all fiscal years. Tiny (<3k rows). Loader normalizes sign (revenue positive) and classifies.
SELECT RTRIM(a.FiscYr) AS fiscal_year, RTRIM(a.Acct) AS acct, RTRIM(a.Sub) AS sub,
       RTRIM(a.LedgerID) AS ledger_id, RTRIM(a.BalanceType) AS balance_type,
       a.PtdBal00, a.PtdBal01, a.PtdBal02, a.PtdBal03, a.PtdBal04, a.PtdBal05,
       a.PtdBal06, a.PtdBal07, a.PtdBal08, a.PtdBal09, a.PtdBal10, a.PtdBal11, a.PtdBal12
FROM AcctHist a
WHERE RTRIM(a.Sub) IN ('0100', '0101')
