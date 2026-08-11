"""Shared CLI plumbing: one logger factory and one set of standard flags.

setup_logging is lifted from Nf_core_plots/common_features.py; add_common_args
is adapted from lib_plot_themes.py's version, keeping the flags that are not
tied to smORF analysis.
"""

from __future__ import annotations

import argparse
import logging


def setup_logging(name: str, level: str = "INFO") -> logging.Logger:
    """Return a stream logger for ``name``, attaching a handler only once.

    The ``if not logger.handlers`` guard is the part people omit, and it is
    what stops doubled log lines when a module is imported twice inside the
    same interpreter — routine under a Nextflow process that imports a helper
    both directly and via a package ``__init__``.

    Args:
        name: Logger name, conventionally the calling module's ``__name__``.
        level: Level name; anything unrecognised falls back to INFO.

    Returns:
        The configured logger. Repeat calls return the same object with the
        level updated and no extra handler.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        fmt = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger


def add_common_args(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach the standard --theme / --scale / --font / --outdir / --log-level flags.

    Defaults are tuned for "publication AND presentation ready": Arial at
    scale 1.0 renders ~11-12pt printed and stays legible on slides.

    Args:
        parser: Parser to mutate.

    Returns:
        The same parser, so the call can be chained.
    """
    parser.add_argument(
        "--theme",
        default="light",
        choices=["light", "dark"],
        help="Internal theme name (default light). Written files carry the "
        "_white / _black suffix, not the theme name.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Multiplier applied to text and marker sizes (default 1.0). Use "
        "~1.3 for slide decks, ~0.8 for journal multi-panels.",
    )
    parser.add_argument(
        "--font",
        default="Arial",
        choices=["Arial", "Helvetica", "Times New Roman"],
        help="Font family (default Arial). Helvetica is selectable but renders "
        "identically — both fall through the same fallback chain to Liberation "
        "Sans / DejaVu Sans on containers without them.",
    )
    parser.add_argument(
        "--outdir",
        default="outputs",
        help="Directory for this script's outputs (default ./outputs).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity, passed to setup_logging (default INFO).",
    )
    return parser
