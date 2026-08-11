"""Guards for the two regressions the Phase 8 dogfood found in v0.1.0.

Both were introduced extracting `labcore.viz` from the original 1407-line
`lib_plot_themes.py`, and both failed silently — which is why they survived a
green test suite and only surfaced when nine real scripts were ported.

See codebase_template/docs/maintenance.md §4.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib as mpl  # noqa: E402
import pytest  # noqa: E402

from labcore.viz import apply_mpl_theme, font_size, resolve_fonts  # noqa: E402
from labcore.viz.theme import role_sizes  # noqa: E402


class TestFontFamilyIsHonoured:
    """v0.1.0 accepted only the classes 'sans'/'serif' and ignored family names.

    A script whose `--font` flag offered Arial / Helvetica / Times New Roman
    rendered sans regardless, with no warning, and the serif house style was
    unreachable from the command line.
    """

    def test_a_named_serif_family_selects_a_serif_face(self):
        serif = resolve_fonts("Times New Roman")
        sans = resolve_fonts("sans")
        assert serif["body"] != sans["body"], (
            "font='Times New Roman' resolved to the same face as font='sans' — "
            "the family name is being ignored"
        )

    def test_a_named_family_leads_its_css_chain(self):
        assert resolve_fonts("Helvetica")["body_css"].startswith("Helvetica")

    def test_serif_class_still_works(self):
        assert resolve_fonts("serif")["body"] != resolve_fonts("sans")["body"]

    def test_an_uninstalled_family_degrades_rather_than_raising(self):
        resolved = resolve_fonts("Nonexistent Face 12345")
        assert resolved["body"], "an unavailable family must fall back, not fail"

    def test_apply_mpl_theme_accepts_a_family_name(self):
        apply_mpl_theme("light", font="Times New Roman")
        serif_family = mpl.rcParams["font.family"]
        apply_mpl_theme("light", font="sans")
        assert serif_family != mpl.rcParams["font.family"], (
            "apply_mpl_theme(font='Times New Roman') set the same family as 'sans'"
        )


class TestContinuousTextSizing:
    """v0.1.0 offered only the named buckets 'paper' and 'deck'.

    The original took a base size in points, so `--text_size 11` and `12` rendered
    identically and everything from 13 up collapsed to 'deck'. Callers that expose
    a point size to users needed an absolute path back.
    """

    @pytest.mark.parametrize("base", [8, 11, 12, 14, 18])
    def test_body_text_lands_on_the_requested_size(self, base):
        assert font_size("tick", base=base) == base
        assert font_size("annot", base=base) == base

    def test_adjacent_sizes_are_distinguishable(self):
        assert role_sizes(base=11) != role_sizes(base=12), (
            "11 pt and 12 pt render identically — the bucket regression is back"
        )

    def test_the_original_offsets_are_preserved(self):
        sizes = role_sizes(base=14)
        assert sizes["axis_label"] == 16.0
        assert sizes["panel_title"] == 18.0
        assert sizes["suptitle"] == 19.0

    def test_base_overrides_the_named_scale(self):
        assert role_sizes("deck", base=8) == role_sizes("paper", base=8)

    def test_named_scales_still_work_when_no_base_is_given(self):
        assert role_sizes("paper") != role_sizes("deck")

    def test_apply_mpl_theme_honours_base_size(self):
        apply_mpl_theme("light", base_size=17)
        assert mpl.rcParams["font.size"] == 17
        assert mpl.rcParams["xtick.labelsize"] == 17
