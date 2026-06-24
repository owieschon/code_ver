"""Unit tests for the statistics library (trustladder.analysis.stats).

These check the estimators against known values and invariants — not just that
they run. Everything here is pure and deterministic.
"""

import math

from trustladder.analysis import stats


# --- normal distribution helpers -------------------------------------------

def test_norm_cdf_known_points():
    assert stats._norm_cdf(0.0) == 0.5
    assert stats._norm_cdf(-10) < 1e-6
    assert stats._norm_cdf(10) > 1 - 1e-6


def test_norm_ppf_matches_textbook_quantiles():
    assert math.isclose(stats._norm_ppf(0.975), 1.959964, abs_tol=1e-3)
    assert math.isclose(stats._norm_ppf(0.5), 0.0, abs_tol=1e-9)
    # round-trip: cdf(ppf(p)) == p
    for p in (0.1, 0.4, 0.8, 0.95):
        assert math.isclose(stats._norm_cdf(stats._norm_ppf(p)), p, abs_tol=1e-4)


# --- Cohen's kappa ----------------------------------------------------------

def test_kappa_perfect_and_empty():
    assert stats.cohens_kappa([(True, True), (False, False)]) == 1.0
    assert stats.cohens_kappa([]) is None


def test_kappa_total_disagreement_is_negative():
    # Graders never agree -> kappa well below zero.
    k = stats.cohens_kappa([(True, False), (False, True)] * 5)
    assert k < 0


def test_kappa_matches_hand_computation():
    # 8 agree-yes, 4 agree-no, 2 row-only-yes, 2 col-only-yes (n=16).
    pairs = [(True, True)] * 8 + [(False, False)] * 4 + \
            [(True, False)] * 2 + [(False, True)] * 2
    po = 12 / 16
    p1a = 10 / 16
    p1b = 10 / 16
    pe = p1a * p1b + (1 - p1a) * (1 - p1b)
    expected = (po - pe) / (1 - pe)
    assert math.isclose(stats.cohens_kappa(pairs), expected, abs_tol=1e-12)


# --- three-outcome decision rule -------------------------------------------

def test_three_outcome_boundaries():
    # CONFIRMED iff lower bound >= floor (inclusive).
    assert stats.three_outcome(20.0, 50.0, 20.0) == "CONFIRMED"
    assert stats.three_outcome(19.9, 50.0, 20.0) == "INDETERMINATE"
    # REFUTED iff upper bound strictly below floor.
    assert stats.three_outcome(-5.0, 19.9, 20.0) == "REFUTED"
    assert stats.three_outcome(-5.0, 20.0, 20.0) == "INDETERMINATE"


# --- paired 2x2 table + Newcombe interval ----------------------------------

def test_paired_counts():
    pairs = [(True, True), (True, False), (True, False),
             (False, True), (False, False)]
    assert stats.paired_counts(pairs) == (1, 2, 1, 1)  # a, b, c, d


def test_newcombe_zero_table_is_zero_width():
    assert stats.newcombe_paired_ci(0, 0, 0, 0, 0.95) == (0.0, 0.0)


def test_newcombe_brackets_a_clear_positive_difference():
    # All row-events, no col-events (b dominates) -> difference strongly positive.
    lo, hi = stats.newcombe_paired_ci(0, 20, 0, 0, 0.95)
    assert lo > 0 and hi <= 100.0


def test_newcombe_symmetric_table_centers_on_zero():
    lo, hi = stats.newcombe_paired_ci(5, 10, 10, 5, 0.95)
    assert lo < 0 < hi


# --- bootstrap intervals ----------------------------------------------------

def test_bootstrap_is_deterministic_given_seed():
    # Same seed must reproduce exactly — the analysis relies on this for
    # auditability. (With small n the interval can even be seed-stable, since
    # the quantiles land on the same order statistics, so we only assert the
    # guaranteed property: identical inputs -> identical output.)
    diffs = [0.2, 0.1, 0.3, 0.0, 0.4, 0.2]
    a = stats.bca_ci(diffs, 500, seed=7, ci_level=0.95)
    b = stats.bca_ci(diffs, 500, seed=7, ci_level=0.95)
    assert a == b


def test_bootstrap_degenerate_input_collapses_to_the_point():
    # Every paired diff is +0.2 -> the mean is 20pp with no spread.
    diffs = [0.2] * 30
    lo, hi = stats.bca_ci(diffs, 500, seed=1, ci_level=0.95)
    assert math.isclose(lo, 20.0, abs_tol=1e-6)
    assert math.isclose(hi, 20.0, abs_tol=1e-6)


def test_bootstrap_brackets_the_mean_for_a_positive_sample():
    diffs = [0.3, 0.2, 0.4, 0.25, 0.35, 0.3, 0.2, 0.4]
    lo, hi = stats.bootstrap_ci(diffs, 1000, seed=3, ci_level=0.95)
    mean_pp = 100.0 * sum(diffs) / len(diffs)
    assert lo <= mean_pp <= hi
    assert lo > 0  # clearly positive sample


# --- within-cell discordance (variance probe) ------------------------------

def test_within_cell_discordance_counts_non_unanimous_cells():
    def rec(task, arm, escape):
        return {"batch": "varprobe", "task_id": task, "arm": arm, "_escape": escape}

    records = [
        rec("t1", "L3", True), rec("t1", "L3", True),    # unanimous -> concordant
        rec("t2", "L3", True), rec("t2", "L3", False),   # split -> discordant
        {"batch": "primary", "task_id": "t9", "arm": "L3", "_escape": True},  # ignored
    ]
    out = stats.within_cell_discordance(records, lambda r: r["_escape"])
    assert out["L3"]["cells"] == 2
    assert out["L3"]["discordant"] == 1
    assert out["_pooled"]["discordance_pp"] == 50.0
