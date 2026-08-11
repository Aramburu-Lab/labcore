"""Tests for labcore.frames."""

from __future__ import annotations

import polars as pl
import pytest

from labcore.frames import DowncastError, downcast, human_bytes, report_size


def test_report_size_prints_bytes_and_human_form(capsys):
    df = pl.DataFrame({"counts": list(range(1000))}, schema={"counts": pl.Int64})
    size = report_size(df)
    out = capsys.readouterr().out
    assert size == df.estimated_size()
    assert str(size) in out
    assert human_bytes(size) in out


def test_downcast_narrows_small_int64_to_int8_and_reports_saving():
    df = pl.DataFrame({"small": [1, -3, 120, 0]}, schema={"small": pl.Int64})
    narrowed, report = downcast(df)

    assert narrowed.schema["small"] == pl.Int8
    assert narrowed.get_column("small").to_list() == [1, -3, 120, 0]
    assert report["columns"]["small"] == {
        "from": "Int64",
        "to": "Int8",
        "bytes_saved": report["columns"]["small"]["bytes_saved"],
    }
    assert report["columns"]["small"]["bytes_saved"] > 0
    assert report["bytes_saved"] == report["bytes_before"] - report["bytes_after"] > 0


def test_downcast_picks_int16_when_int8_would_not_fit():
    df = pl.DataFrame({"mid": [0, 300]}, schema={"mid": pl.Int64})
    narrowed, _ = downcast(df)
    assert narrowed.schema["mid"] == pl.Int16


def test_downcast_narrows_unsigned_ladder():
    df = pl.DataFrame({"depth": [0, 250]}, schema={"depth": pl.UInt64})
    narrowed, _ = downcast(df)
    assert narrowed.schema["depth"] == pl.UInt8


def test_downcast_raises_under_strict_when_value_exceeds_target_width():
    df = pl.DataFrame({"reads": [1, 40_000]}, schema={"reads": pl.Int64})
    with pytest.raises(DowncastError, match="do not fit"):
        downcast(df, strict=True, target={"reads": pl.Int16})


def test_downcast_skips_impossible_target_when_not_strict():
    df = pl.DataFrame({"reads": [1, 40_000]}, schema={"reads": pl.Int64})
    narrowed, report = downcast(df, strict=False, target={"reads": pl.Int16})
    assert narrowed.schema["reads"] == pl.Int64
    assert "do not fit" in report["skipped"]["reads"]


def test_downcast_leaves_floats_alone_when_strict_and_narrows_when_not():
    df = pl.DataFrame({"tpm": [1.5, 2.5]}, schema={"tpm": pl.Float64})
    strict_frame, strict_report = downcast(df, strict=True)
    assert strict_frame.schema["tpm"] == pl.Float64
    assert "lossy" in strict_report["skipped"]["tpm"]

    loose_frame, _ = downcast(df, strict=False)
    assert loose_frame.schema["tpm"] == pl.Float32


def test_downcast_handles_null_only_and_empty_columns():
    df = pl.DataFrame(
        {"all_null": [None, None], "labelled": ["a", "b"]},
        schema={"all_null": pl.Int64, "labelled": pl.Utf8},
    )
    narrowed, report = downcast(df)
    assert narrowed.schema["all_null"] == pl.Int64
    assert narrowed.schema["labelled"] == pl.Utf8
    assert "no non-null values" in report["skipped"]["all_null"]

    empty = pl.DataFrame({"nothing": []}, schema={"nothing": pl.Int64})
    empty_narrowed, _ = downcast(empty)
    assert empty_narrowed.schema["nothing"] == pl.Int64


def test_downcast_keeps_nulls_after_narrowing():
    df = pl.DataFrame({"sparse": [None, 5, None]}, schema={"sparse": pl.Int64})
    narrowed, _ = downcast(df)
    assert narrowed.schema["sparse"] == pl.Int8
    assert narrowed.get_column("sparse").to_list() == [None, 5, None]


def test_human_bytes_units():
    assert human_bytes(512) == "512 B"
    assert human_bytes(2048) == "2.0 KiB"
    assert human_bytes(-1024) == "-1.0 KiB"
