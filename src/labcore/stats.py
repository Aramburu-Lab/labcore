"""Pairwise significance tests and star labels for figure annotation.

Extracted from Phase_4_downstream/bin/lib_plot_themes.py (lines 1093-1407):
these are statistics, not styling, and plotting code should not be the only
place they live.

scipy is an optional dependency (``pip install labcore[stats]``). Every test
function imports it lazily so that importing this module — or calling
``pvalue_to_label`` — costs nothing on a scipy-less container.

All test functions return ``None`` rather than raising when the input is too
small or degenerate; ``pvalue_to_label(None)`` maps that to "ns", which is the
conservative report.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

# Thresholds and their labels, strictly ascending; the first match wins.
STAR_THRESHOLDS: tuple[tuple[float, str], ...] = ((0.001, "***"), (0.01, "**"), (0.05, "*"))

# Mann-Whitney has zero power below this combined sample size, so report "ns".
MIN_MWU_TOTAL_N = 4

# Welch's t and Wilcoxon both need at least two usable observations per group.
MIN_GROUP_N = 2


def _scipy_stats() -> Any:
    """Import scipy.stats, or raise with an actionable install hint."""
    try:
        from scipy import stats
    except ImportError as exc:
        raise ImportError(
            "scipy is required for labcore.stats significance tests. "
            "Install it with: pip install 'labcore[stats]'"
        ) from exc
    return stats


def _numpy() -> Any:
    """Import numpy (guaranteed present once scipy is)."""
    import numpy as np

    return np


def _drop_nan(values: list[float]) -> Any:
    """Return values as a float array with NaNs removed."""
    np = _numpy()
    arr = np.asarray(list(values), dtype=float)
    return arr[~np.isnan(arr)]


def _is_nan(value: float) -> bool:
    """True when value is NaN, tolerating numpy scalars."""
    return math.isnan(float(value))


def pvalue_to_label(p: float | None) -> str:
    """Map a p-value to an APA-ish star annotation.

    p >= 0.05 -> "ns" (not significant; noted, not omitted), p < 0.05 -> "*",
    p < 0.01 -> "**", p < 0.001 -> "***". None and NaN are unmeasurable and
    are reported as "ns" rather than silently dropped.

    Args:
        p: p-value, or None when the test could not be run.

    Returns:
        One of "ns", "*", "**", "***".
    """
    if p is None:
        return "ns"
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "ns"
    if math.isnan(p):
        return "ns"
    for threshold, label in STAR_THRESHOLDS:
        if p < threshold:
            return label
    return "ns"


def mann_whitney_p_per_pair(
    values_a: list[float],
    values_b: list[float],
    alternative: str = "two-sided",
) -> float | None:
    """Two-sample Mann-Whitney U (rank-sum) p-value.

    Non-parametric alternative to Welch's t — preferred when groups are tiny
    (n=2) and the distributional assumptions of t-tests are doubtful.

    Args:
        values_a: Observations in group A. NaNs are dropped.
        values_b: Observations in group B. NaNs are dropped.
        alternative: One of "two-sided", "less", "greater".

    Returns:
        The p-value, or None when the combined sample is too small (< 4, where
        MWU has zero power) or the test fails.

    Raises:
        ImportError: If scipy is not installed.
    """
    stats = _scipy_stats()
    a = _drop_nan(values_a)
    b = _drop_nan(values_b)
    if a.size < 1 or b.size < 1:
        return None
    if a.size + b.size < MIN_MWU_TOTAL_N:
        return None
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=".*Exact p-value calculation does not work if there are tied values.*",
                category=UserWarning,
            )
            res = stats.mannwhitneyu(a, b, alternative=alternative)
        p = float(res.pvalue)
    except ValueError:
        return None
    if _is_nan(p):
        return None
    return p


def wilcoxon_p_per_pair(values_a: list[float], values_b: list[float]) -> float | None:
    """Paired Wilcoxon signed-rank p-value (two-sided).

    Args:
        values_a: First member of each pair.
        values_b: Second member of each pair; must be the same length as
            ``values_a``. Pairs containing a NaN are stripped.

    Returns:
        The p-value, or None when lengths differ, fewer than 2 valid pairs
        survive (Wilcoxon needs >= 2 non-zero differences), or all differences
        are zero (identical paired data — no test possible).

    Raises:
        ImportError: If scipy is not installed.
    """
    stats = _scipy_stats()
    np = _numpy()
    if len(values_a) != len(values_b):
        return None
    a = np.asarray(values_a, dtype=float)
    b = np.asarray(values_b, dtype=float)
    valid = np.isfinite(a) & np.isfinite(b)
    a = a[valid]
    b = b[valid]
    if a.size < MIN_GROUP_N:
        return None
    if np.allclose(a - b, 0):
        return None
    try:
        res = stats.wilcoxon(a, b, alternative="two-sided", zero_method="wilcox")
        p = float(res.pvalue)
    except ValueError:
        return None
    if _is_nan(p):
        return None
    return p


def fisher_exact_p(a: int, b: int, c: int, d: int, alternative: str = "two-sided") -> float | None:
    """Fisher's exact test p-value for a 2x2 count table.

    The table is ``[[a, b], [c, d]]``: rows are event present / absent, columns
    are group A / group B. Use it for "is the proportion of events different
    between groups?" on small counts where chi-squared is unreliable (any
    expected cell < 5).

    Args:
        a: Group A, event present.
        b: Group B, event present.
        c: Group A, event absent.
        d: Group B, event absent.
        alternative: One of "two-sided", "less", "greater".

    Returns:
        The p-value, or None for negative counts or a failed test.

    Raises:
        ImportError: If scipy is not installed.
    """
    stats = _scipy_stats()
    if any(v < 0 for v in (a, b, c, d)):
        return None
    try:
        _, p = stats.fisher_exact([[a, b], [c, d]], alternative=alternative)
    except ValueError:
        return None
    if _is_nan(p):
        return None
    return float(p)


def _welch_t_and_df(a: Any, b: Any) -> tuple[float, float] | None:
    """Welch t statistic and Satterthwaite df, or None when undefined."""
    a_mean = float(a.mean())
    b_mean = float(b.mean())
    a_var = float(((a - a_mean) ** 2).sum() / (a.size - 1))
    b_var = float(((b - b_mean) ** 2).sum() / (b.size - 1))
    if a_var == 0.0 and b_var == 0.0:
        # Both constant — Welch's t undefined. If the means agree there is no
        # difference; if not, the test cannot quantify it. "ns" is conservative.
        return None

    se = math.sqrt(a_var / a.size + b_var / b.size)
    if se == 0.0 or not math.isfinite(se):
        return None

    num = (a_var / a.size + b_var / b.size) ** 2
    denom_a = (a_var / a.size) ** 2 / max(a.size - 1, 1)
    denom_b = (b_var / b.size) ** 2 / max(b.size - 1, 1)
    if (denom_a + denom_b) <= 0.0:
        return None
    df = num / (denom_a + denom_b)
    if not math.isfinite(df) or df <= 0.0:
        return None
    return (a_mean - b_mean) / se, df


def welch_p_per_pair(values_a: list[float], values_b: list[float]) -> float | None:
    """Two-sided Welch's t-test p-value (unequal variances).

    The t statistic is computed manually with two-pass mean subtraction rather
    than via ``scipy.stats.ttest_ind``: scipy's moment-based variance loses
    precision on inputs like [3, 3] (two replicates with identical counts of a
    rare category) and emits a catastrophic-cancellation warning at n=2. The
    manual path gives the same answer for well-behaved data and handles the
    zero-variance case explicitly. Suppression of the residual warning is
    targeted by message so unrelated RuntimeWarnings still surface.

    Args:
        values_a: Observations in group A. NaNs are dropped.
        values_b: Observations in group B. NaNs are dropped.

    Returns:
        The p-value, or None when either group has n < 2, both groups are
        constant (Welch's t undefined), or the degrees of freedom are not
        finite.

    Raises:
        ImportError: If scipy is not installed.
    """
    stats = _scipy_stats()
    a = _drop_nan(values_a)
    b = _drop_nan(values_b)
    if a.size < MIN_GROUP_N or b.size < MIN_GROUP_N:
        return None

    t_and_df = _welch_t_and_df(a, b)
    if t_and_df is None:
        return None
    t_stat, df = t_and_df

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*Precision loss occurred in moment calculation.*",
            category=RuntimeWarning,
        )
        p = 2.0 * stats.t.sf(abs(t_stat), df=df)
    if not math.isfinite(p):
        return None
    return float(p)
