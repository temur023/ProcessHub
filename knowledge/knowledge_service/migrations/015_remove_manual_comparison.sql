-- Manual comparison stages are replaced by automatic analysis (analysis_runs)
-- with per-finding expert decisions. The table goes only if it holds no
-- history; otherwise it stays untouched, read by nothing.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM comparison_workflows) THEN
        DROP TABLE comparison_workflows;
        DROP FUNCTION protect_comparison_workflow_identity();
    END IF;
END;
$$;
