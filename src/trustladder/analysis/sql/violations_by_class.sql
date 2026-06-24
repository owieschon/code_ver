-- Policy violations by class per arm — a runs-to-violations join.
-- "survived_to_done" counts violations the gate let through to a "done" claim.
SELECT r.arm,
       v.class,
       COUNT(*)                AS n_violations,
       SUM(v.survived_to_done) AS survived_to_done
FROM violations v
JOIN runs r ON r.run_id = v.run_id
WHERE r.batch = 'primary'
GROUP BY r.arm, v.class
ORDER BY CASE r.arm WHEN 'L0' THEN 0 WHEN 'L1' THEN 1
                    WHEN 'SHAM' THEN 2 WHEN 'L3' THEN 3 ELSE 4 END,
         v.class;
