"""Contract tests for labcore.viz: file counts, suffixes, theme spellings."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import polars as pl  # noqa: E402
import pytest  # noqa: E402

from labcore.viz import (  # noqa: E402
    apply_mpl_theme,
    bg_name,
    categorical_colors,
    normalize_theme,
    panel_grid,
    save_figure,
)


@pytest.fixture
def mpl_fig():
    apply_mpl_theme("light", scale="paper")
    fig, ax = plt.subplots()
    ax.plot([0, 1, 2], [1, 4, 2], label="series")
    ax.set_xlabel("x")
    ax.legend()
    yield fig
    plt.close(fig)


@pytest.fixture
def plotly_fig():
    return go.Figure(go.Scatter(x=[0, 1, 2], y=[1, 4, 2]))


def test_save_figure_writes_two_pdfs_by_default(mpl_fig, tmp_path):
    written = save_figure(mpl_fig, "out_demo_depth", tmp_path)
    assert len(written) == 2
    assert [p.name for p in written] == ["out_demo_depth_white.pdf",
                                         "out_demo_depth_black.pdf"]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        p.name for p in written)


def test_interactive_adds_two_html_files(mpl_fig, plotly_fig, tmp_path):
    written = save_figure((mpl_fig, plotly_fig), "out_demo_depth", tmp_path,
                          interactive=True)
    assert len(written) == 4
    assert sorted(p.name for p in written) == [
        "out_demo_depth_black.html", "out_demo_depth_black.pdf",
        "out_demo_depth_white.html", "out_demo_depth_white.pdf",
    ]


def test_interactive_without_a_plotly_figure_is_an_error(mpl_fig, tmp_path):
    with pytest.raises(TypeError):
        save_figure(mpl_fig, "out_demo_depth", tmp_path, interactive=True)


def test_suffixes_are_white_and_black_not_light_and_dark(mpl_fig, tmp_path):
    written = save_figure(mpl_fig, "out_demo_depth", tmp_path)
    names = [p.name for p in written]
    assert all("_light" not in n and "_dark" not in n for n in names)
    assert bg_name("light") == "white"
    assert bg_name("dark") == "black"


def test_normalize_theme_maps_all_four_spellings():
    assert normalize_theme("light") == "light"
    assert normalize_theme("white") == "light"
    assert normalize_theme("dark") == "dark"
    assert normalize_theme("black") == "dark"


def test_csv_sidecar_written_once_without_a_theme_suffix(mpl_fig, tmp_path):
    frame = pl.DataFrame({"sample": ["a", "b"], "reads": [10, 20]})
    written = save_figure(mpl_fig, "out_demo_depth", tmp_path, data=frame)
    csvs = [p for p in written if p.suffix == ".csv"]
    assert len(csvs) == 1
    assert csvs[0].name == "out_demo_depth.csv"
    assert list(tmp_path.glob("*.csv")) == csvs


def test_categorical_colors_wrap_past_eight():
    colors = categorical_colors("light", 11)
    assert len(colors) == 11
    assert colors[8:] == colors[:3]
    assert categorical_colors("dark", 3) != categorical_colors("light", 3)


def test_panel_grid_blanks_spares_and_shares_limits():
    fig, axes = plt.subplots(2, 2)
    axes[0, 0].plot([0, 1], [0, 5])
    axes[0, 1].plot([0, 1], [0, 1])
    axes[1, 0].plot([0, 1], [0, 3])
    panel_grid(fig, axes, n_used=3, n_cols=2)
    used = [axes[0, 0], axes[0, 1], axes[1, 0]]
    assert len({ax.get_ylim() for ax in used}) == 1
    assert not axes[1, 1].axison
    plt.close(fig)
