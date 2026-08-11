"""Polars frame helpers: size reporting and range-checked numeric downcasting.

Polars only. pandas/Arrow conversions live in ``labcore.io`` so this module stays
importable with the base dependency set.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

SIGNED_LADDER: tuple[pl.DataType, ...] = (pl.Int8, pl.Int16, pl.Int32, pl.Int64)
UNSIGNED_LADDER: tuple[pl.DataType, ...] = (pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64)

_INT_LIMITS: dict[pl.DataType, tuple[int, int]] = {
    pl.Int8: (-128, 127),
    pl.Int16: (-32_768, 32_767),
    pl.Int32: (-2_147_483_648, 2_147_483_647),
    pl.Int64: (-9_223_372_036_854_775_808, 9_223_372_036_854_775_807),
    pl.UInt8: (0, 255),
    pl.UInt16: (0, 65_535),
    pl.UInt32: (0, 4_294_967_295),
    pl.UInt64: (0, 18_446_744_073_709_551_615),
}

_WIDTHS: dict[pl.DataType, int] = {
    pl.Int8: 1, pl.UInt8: 1,
    pl.Int16: 2, pl.UInt16: 2,
    pl.Int32: 4, pl.UInt32: 4, pl.Float32: 4,
    pl.Int64: 8, pl.UInt64: 8, pl.Float64: 8,
}

_FLOAT32_MAX = 3.4028234663852886e38
_KIB = 1024


class DowncastError(ValueError):
    """Raised when a requested target dtype cannot hold a column's actual values."""


def report_size(df: pl.DataFrame) -> int:
    """Print and return the estimated in-memory size of a frame.

    Args:
        df: Frame to measure.

    Returns:
        Estimated size in bytes.
    """
    size = df.estimated_size()
    print(f"Estimated size: {size} bytes ({human_bytes(size)})")
    return size


def human_bytes(size: int) -> str:
    """Format a byte count with a binary unit suffix.

    Args:
        size: Byte count; may be negative (a saving of the opposite sign).

    Returns:
        A string such as ``'1.4 MiB'``.
    """
    sign = "-" if size < 0 else ""
    value = float(abs(size))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < _KIB or unit == "TiB":
            digits = 0 if unit == "B" else 1
            return f"{sign}{value:.{digits}f} {unit}"
        value /= _KIB
    raise AssertionError("unreachable")


def downcast(
    df: pl.DataFrame,
    *,
    strict: bool = True,
    target: Mapping[str, pl.DataType] | None = None,
) -> tuple[pl.DataFrame, dict]:
    """Narrow numeric columns to the smallest dtype that actually fits their values.

    Every column's real min/max is measured before narrowing, so no value is ever
    silently wrapped. Signed ints walk Int64->Int32->Int16->Int8, unsigned ints the
    UInt ladder, and non-numeric columns are left untouched. Null-only and empty
    columns are skipped rather than narrowed — there is no range to justify a width.

    Float64->Float32 is LOSSY (it drops mantissa bits even for in-range values), so
    floats are only narrowed when ``strict=False``.

    Args:
        df: Frame to narrow.
        strict: When True, a ``target`` dtype that cannot hold the column's values
            raises instead of being skipped, and floats are left alone.
        target: Optional explicit per-column target dtype, overriding the automatic
            choice. Columns not named here still narrow automatically.

    Returns:
        The narrowed frame and a report with ``bytes_before``, ``bytes_after``,
        ``bytes_saved``, a per-column ``columns`` map, and a ``skipped`` map of
        column name to reason.

    Raises:
        DowncastError: With ``strict=True``, when a ``target`` dtype is too narrow
            for the column's actual values.
    """
    targets = dict(target or {})
    columns: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    casts: list[pl.Expr] = []

    for name in df.columns:
        series = df.get_column(name)
        chosen, reason = _plan_column(series, targets.get(name), strict=strict)
        if chosen is None:
            skipped[name] = reason
            continue
        saved = series.estimated_size() - series.cast(chosen).estimated_size()
        columns[name] = {"from": str(series.dtype), "to": str(chosen), "bytes_saved": saved}
        casts.append(pl.col(name).cast(chosen))

    narrowed = df.with_columns(casts) if casts else df
    before, after = df.estimated_size(), narrowed.estimated_size()
    report = {
        "bytes_before": before,
        "bytes_after": after,
        "bytes_saved": before - after,
        "columns": columns,
        "skipped": skipped,
    }
    return narrowed, report


def _plan_column(
    series: pl.Series,
    target: pl.DataType | None,
    *,
    strict: bool,
) -> tuple[pl.DataType | None, str]:
    """Pick a narrower dtype for one series, or return None plus the reason it was skipped."""
    dtype = series.dtype
    if not dtype.is_numeric() or dtype.is_decimal():
        return None, "not a plain numeric column"

    low, high = series.min(), series.max()
    if low is None or high is None:
        return None, "no non-null values to bound a narrower dtype"

    if target is not None:
        return _plan_target(series, target, low, high, strict=strict)

    if dtype.is_float():
        return _plan_float(dtype, low, high, strict=strict)

    ladder = SIGNED_LADDER if dtype.is_signed_integer() else UNSIGNED_LADDER
    chosen = _smallest_int(ladder, low, high)
    if chosen is None or _width(chosen) >= _width(dtype):
        return None, "already the smallest dtype that fits"
    return chosen, ""


def _plan_float(
    dtype: pl.DataType,
    low: float,
    high: float,
    *,
    strict: bool,
) -> tuple[pl.DataType | None, str]:
    """Decide whether a float column may narrow to Float32, which drops mantissa bits."""
    if strict:
        return None, "float narrowing is lossy; pass strict=False to allow it"
    if dtype == pl.Float32:
        return None, "already the smallest float dtype"
    if not _fits_float32(low, high):
        return None, "values exceed Float32 range"
    return pl.Float32, ""


def _plan_target(
    series: pl.Series,
    target: pl.DataType,
    low: float,
    high: float,
    *,
    strict: bool,
) -> tuple[pl.DataType | None, str]:
    """Validate an explicitly requested target dtype against the series' actual range."""
    fits = _fits_float32(low, high) if target == pl.Float32 else _fits_int(target, low, high)
    if fits:
        return target, ""
    reason = f"values [{low}, {high}] do not fit {target}"
    if strict:
        raise DowncastError(
            f"Column '{series.name}': {reason}. Widen the target dtype, or pass "
            f"strict=False to leave this column at {series.dtype}."
        )
    return None, reason


def _fits_int(dtype: pl.DataType, low: float, high: float) -> bool:
    """Whether an integer dtype's limits contain the given range."""
    limits = _INT_LIMITS.get(dtype)
    return limits is not None and limits[0] <= low and high <= limits[1]


def _fits_float32(low: float, high: float) -> bool:
    """Whether a range is within Float32's representable magnitude."""
    return abs(low) <= _FLOAT32_MAX and abs(high) <= _FLOAT32_MAX


def _smallest_int(ladder: tuple[pl.DataType, ...], low: float, high: float) -> pl.DataType | None:
    """First dtype on the ladder whose limits contain the range."""
    for dtype in ladder:
        if _fits_int(dtype, low, high):
            return dtype
    return None


def _width(dtype: pl.DataType) -> int:
    """Byte width of a fixed-width numeric dtype."""
    return _WIDTHS.get(dtype, 8)
