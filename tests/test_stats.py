from __future__ import annotations

import math

import pytest

from labcore.stats import (
    fisher_exact_p,
    mann_whitney_p_per_pair,
    pvalue_to_label,
    welch_p_per_pair,
    wilcoxon_p_per_pair,
)


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (0.0009999, "***"),
        (0.001, "**"),
        (0.0099999, "**"),
        (0.01, "*"),
        (0.0499999, "*"),
        (0.05, "ns"),
        (1.0, "ns"),
        (0.0, "***"),
    ],
)
def test_pvalue_to_label_boundaries(p, expected):
    assert pvalue_to_label(p) == expected


@pytest.mark.parametrize("p", [None, float("nan"), "nonsense", object()])
def test_pvalue_to_label_unmeasurable_is_ns(p):
    assert pvalue_to_label(p) == "ns"


def test_mann_whitney_needs_four_observations():
    assert mann_whitney_p_per_pair([1.0], [2.0]) is None
    p = mann_whitney_p_per_pair([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert p is not None and 0.0 < p < 1.0


def test_wilcoxon_rejects_unequal_lengths_and_zero_diffs():
    assert wilcoxon_p_per_pair([1.0, 2.0], [1.0]) is None
    assert wilcoxon_p_per_pair([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) is None
    assert wilcoxon_p_per_pair([1.0, 2.0, 3.0, 4.0], [2.0, 3.0, 5.0, 9.0]) is not None


def test_fisher_exact_matches_known_table():
    p = fisher_exact_p(1, 9, 11, 3)
    assert p is not None and math.isclose(p, 0.0027594, rel_tol=1e-3)
    assert fisher_exact_p(-1, 1, 1, 1) is None


def test_welch_handles_degenerate_inputs():
    assert welch_p_per_pair([1.0], [2.0, 3.0]) is None
    assert welch_p_per_pair([3.0, 3.0], [3.0, 3.0]) is None
    p = welch_p_per_pair([1.0, 2.0, 3.0], [11.0, 12.0, 13.0])
    assert p is not None and p < 0.05
