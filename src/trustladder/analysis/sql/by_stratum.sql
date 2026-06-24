-- Escape rate split by manifold-distance stratum (typical vs novel) — the
-- descriptive view behind the H1b "does enforcement matter more on novel
-- work?" exploratory question.
WITH escaped_runs AS (
    SELECT DISTINCT e.run_id
    FROM escapes e
    JOIN sev_rank s ON s.sev = e.sev
    WHERE e.escaped = 1 AND s.rank >= 2
)
SELECT r.arm,
       r.stratum,
       COUNT(*) AS n,
       ROUND(100.0 * AVG(CASE WHEN x.run_id IS NOT NULL THEN 1 ELSE 0 END), 1)
           AS escape_pct
FROM runs r
LEFT JOIN escaped_runs x ON x.run_id = r.run_id
WHERE r.batch = 'primary'
GROUP BY r.arm, r.stratum
ORDER BY CASE r.arm WHEN 'L0' THEN 0 WHEN 'L1' THEN 1
                    WHEN 'SHAM' THEN 2 WHEN 'L3' THEN 3 ELSE 4 END,
         r.stratum;
