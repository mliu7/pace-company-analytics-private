-- sl.salespersons — 66 codes; OT/VOT are non-commission placeholders, *98/*99 are RMA codes.
SELECT RTRIM(s.SlsperId) AS salesperson_code, RTRIM(s.Name) AS name, RTRIM(s.Territory) AS territory,
       s.CmmnPct AS commission_pct, s.Crtd_DateTime AS sl_created_at, s.LUpd_DateTime AS sl_updated_at
FROM Salesperson s
ORDER BY s.SlsperId
