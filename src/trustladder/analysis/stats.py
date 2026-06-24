"""Statistics for the confirmatory and exploratory analysis.

Pure functions on plain numbers and record dicts — no dependency on the rest of
the pipeline, deterministic given a seed, standard-library only. Kept separate so
the estimators can be read and unit-tested on their own.
"""

import math
import random

def cohens_kappa(pairs):
    """Binary Cohen's kappa over (g1, g2) label pairs. Degenerate
    marginals (pe==1): kappa := 1.0 iff perfect agreement, else 0.0
    (documented convention)."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    p1a = sum(1 for a, _ in pairs if a) / n
    p1b = sum(1 for _, b in pairs if b) / n
    pe = p1a * p1b + (1 - p1a) * (1 - p1b)
    if abs(1 - pe) < 1e-12:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p):
    """Inverse standard-normal CDF (Acklam's rational approximation).
    Deterministic, stdlib-only; |abs error| < 1.2e-9 for 0 < p < 1."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return ((((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])
                / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1))
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return ((((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q
                / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1))
    q = math.sqrt(-2 * math.log(1 - p))
    return -((((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])
             / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1))


def _pctl(sorted_vals, q):
    """Floor-rank quantile (matches the legacy percentile convention)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    return sorted_vals[min(n - 1, int(math.floor(q * n)))]


def _bootstrap_means(diffs, n_resamples, seed):
    """Sorted bootstrap distribution of the mean paired diff. Shared by
    the percentile and BCa readers so both see the IDENTICAL resample
    set (deterministic seed); only the quantiles read differ."""
    rng = random.Random(seed)
    n = len(diffs)
    means = [sum(diffs[rng.randrange(n)] for _ in range(n)) / n
             for _ in range(n_resamples)]
    means.sort()
    return means


def bootstrap_ci(diffs, n_resamples, seed, ci_level):
    """LEGACY percentile bootstrap CI over task-level paired diffs.
    Retained for reported sensitivity only — the percentile method
    under-covers at small n, so it is NO LONGER the primary confirmatory
    interval. Deterministic seed."""
    means = _bootstrap_means(diffs, n_resamples, seed)
    alpha = (1 - ci_level) / 2
    return 100.0 * _pctl(means, alpha), 100.0 * _pctl(means, 1 - alpha)


def bca_ci(diffs, n_resamples, seed, ci_level):
    """Bias-corrected & accelerated (BCa) bootstrap CI for the mean
    paired diff (Efron 1987). Better small-n coverage than percentile;
    still a bootstrap interval. Shares the resample set with
    bootstrap_ci. Degenerate cases (no bootstrap spread, or zero
    jackknife variance) fall back to the percentile quantiles.
    Deterministic seed."""
    n = len(diffs)
    theta = sum(diffs) / n
    means = _bootstrap_means(diffs, n_resamples, seed)
    alpha = (1 - ci_level) / 2
    below = sum(1 for m in means if m < theta)        # bias correction z0
    prop = below / len(means)
    if prop <= 0.0 or prop >= 1.0:                     # degenerate -> percentile
        return 100.0 * _pctl(means, alpha), 100.0 * _pctl(means, 1 - alpha)
    z0 = _norm_ppf(prop)
    total = sum(diffs)                                 # acceleration via jackknife
    jack = [(total - x) / (n - 1) for x in diffs]
    jbar = sum(jack) / n
    num = sum((jbar - j) ** 3 for j in jack)
    den = 6.0 * (sum((jbar - j) ** 2 for j in jack)) ** 1.5
    a = (num / den) if den > 0 else 0.0
    zlo, zhi = _norm_ppf(alpha), _norm_ppf(1 - alpha)

    def adj(z):
        denom = 1 - a * (z0 + z)
        return _norm_cdf(z0 + (z0 + z) / (denom if denom != 0 else 1e-12))

    return 100.0 * _pctl(means, adj(zlo)), 100.0 * _pctl(means, adj(zhi))


def _wilson(x, n, z):
    """Wilson score interval for a single proportion (stable from n~10)."""
    if n == 0:
        return 0.0, 0.0
    phat = x / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return center - half, center + half


def paired_counts(pairs):
    """2x2 paired table (a,b,c,d) from (row_event, col_event) bool pairs:
    a=both, b=row-only, c=col-only, d=neither. The marginal difference
    p_row - p_col = (b - c)/n."""
    a = sum(1 for r, col in pairs if r and col)
    b = sum(1 for r, col in pairs if r and not col)
    c = sum(1 for r, col in pairs if not r and col)
    d = sum(1 for r, col in pairs if not r and not col)
    return a, b, c, d


def newcombe_paired_ci(a, b, c, d, ci_level):
    """Newcombe (1998) MOVER-Wilson interval for the difference of two
    PAIRED proportions p_row - p_col over the 2x2 table [[a,b],[c,d]]
    (p_row=(a+b)/n, p_col=(a+c)/n, difference=(b-c)/n). Analytic small-n
    cross-check on the bootstrap. Returns (lo_pp, hi_pp)."""
    n = a + b + c + d
    if n == 0:
        return 0.0, 0.0
    z = _norm_ppf(1 - (1 - ci_level) / 2)
    p1, p2 = (a + b) / n, (a + c) / n
    delta = p1 - p2
    l1, u1 = _wilson(a + b, n, z)
    l2, u2 = _wilson(a + c, n, z)
    A = (a + b) * (c + d) * (a + c) * (b + d)
    phi = ((a * d - b * c) / math.sqrt(A)) if A > 0 else 0.0
    lo = delta - math.sqrt(max(0.0, (p1 - l1) ** 2
                               - 2 * phi * (p1 - l1) * (u2 - p2)
                               + (u2 - p2) ** 2))
    hi = delta + math.sqrt(max(0.0, (u1 - p1) ** 2
                               - 2 * phi * (u1 - p1) * (p2 - l2)
                               + (p2 - l2) ** 2))
    return 100.0 * lo, 100.0 * hi


def within_cell_discordance(records, metric_fn):
    """NS-6 / M8: per-arm run-to-run noise floor over the varprobe batch.
    Groups varprobe records by (task_id, arm); a cell
    is DISCORDANT iff its reps do NOT all agree on metric_fn (e.g.
    is_m1_escape). Per-arm noise floor = fraction of that arm's probe cells
    that are discordant. Returns {arm: {...}, '_pooled': {...}}."""
    cells = {}
    for r in records:
        if r.get("batch") != "varprobe":
            continue
        cells.setdefault((r["task_id"], r["arm"]), []).append(bool(metric_fn(r)))
    by_arm = {}
    for (task, arm), outcomes in cells.items():
        d = by_arm.setdefault(arm, {"cells": 0, "discordant": 0, "reps_seen": set()})
        d["cells"] += 1
        d["reps_seen"].add(len(outcomes))
        if len(set(outcomes)) > 1:                 # not unanimous -> discordant
            d["discordant"] += 1
    out, tot_cells, tot_disc = {}, 0, 0
    for arm in sorted(by_arm):
        d = by_arm[arm]
        tot_cells += d["cells"]
        tot_disc += d["discordant"]
        out[arm] = {"cells": d["cells"], "discordant": d["discordant"],
                    "discordance_pp": (100.0 * d["discordant"] / d["cells"])
                                      if d["cells"] else None,
                    "reps_per_cell": sorted(d["reps_seen"])}
    out["_pooled"] = {"cells": tot_cells, "discordant": tot_disc,
                      "discordance_pp": (100.0 * tot_disc / tot_cells)
                                        if tot_cells else None}
    return out


def three_outcome(ci_lo_pp, ci_hi_pp, floor_pp):
    """Three-outcome decision rule. Boundary convention (documented):
    CONFIRMED iff ci_lo >= floor ('by at least the floor'); REFUTED iff
    ci_hi < floor (CI ENTIRELY below)."""
    if ci_lo_pp >= floor_pp:
        return "CONFIRMED"
    if ci_hi_pp < floor_pp:
        return "REFUTED"
    return "INDETERMINATE"

