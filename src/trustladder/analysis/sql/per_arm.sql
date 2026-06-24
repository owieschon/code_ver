-- Per-arm completion (M0) and escape (M1) rates.
-- Controls (non-'primary' batches) are excluded from M0/M1, matching the
-- registered analysis.
WITH escaped_runs AS (
    -- M1 escape: a defect that SURVIVED to ship at severity >= SEV2.
    SELECT DISTINCT e.run_id
    FROM escapes e
    JOIN sev_rank s ON s.sev = e.sev
    WHERE e.escaped = 1 AND s.rank >= 2          -- SEV2+ is the primary threshold
),
graded AS (
    SELECT r.arm,
           r.completion,
           CASE WHEN x.run_id IS NOT NULL THEN 1 ELSE 0 END AS escaped
    FROM runs r
    LEFT JOIN escaped_runs x ON x.run_id = r.run_id
    WHERE r.batch = 'primary'
)
SELECT arm,
       COUNT(*)                          AS n,
       ROUND(100.0 * AVG(completion), 1) AS completion_pct,
       ROUND(100.0 * AVG(escaped), 1)    AS escape_pct
FROM graded
GROUP BY arm
-- order along the trust ladder, not alphabetically
ORDER BY CASE arm WHEN 'L0' THEN 0 WHEN 'L1' THEN 1
                  WHEN 'SHAM' THEN 2 WHEN 'L3' THEN 3 ELSE 4 END;
