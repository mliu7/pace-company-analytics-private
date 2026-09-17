-- sl.project_budget_view — SL's own per-project summary view (PJPrjBgt). Secondary checksum only:
-- ACT_Margin / Total_Budget_Margin must equal the Pace Company Analytics GP figures (labelling differs: SL puts BURDEN in ACT_Exp).
-- Not a source of truth: the view definition is not readable by the read-only login.
SELECT RTRIM(v.Project) AS project_number_raw,
       v.ACT_Hrs, v.ACT_Rev, v.ACT_RevAdj, v.ACT_Labor, v.ACT_Exp, v.ACT_Margin, v.ACT_MarginPct,
       v.COMMIT_Hrs, v.COMMIT_Labor, v.COMMIT_Exp,
       v.EAC_Hrs, v.EAC_Rev, v.EAC_Labor, v.EAC_Exp, v.EAC_Margin, v.EAC_MarginPct,
       v.Total_Budget_Hrs, v.Total_Budget_Rev, v.Total_Budget_Labor, v.Total_Budget_Exp, v.Total_Budget_Margin, v.Total_Budget_MarginPct,
       RTRIM(v.Manager1) AS project_manager_key, RTRIM(v.Manager2) AS division_head_key
FROM PJPrjBgt v
ORDER BY v.Project
