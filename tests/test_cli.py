from __future__ import annotations

import argparse
import logging

from labcore.cli import add_common_args, setup_logging


def test_setup_logging_does_not_duplicate_handlers():
    name = "labcore.test.doubled"
    logging.getLogger(name).handlers.clear()
    first = setup_logging(name)
    second = setup_logging(name, level="DEBUG")
    assert first is second
    assert len(second.handlers) == 1
    assert second.level == logging.DEBUG


def test_setup_logging_unknown_level_falls_back_to_info():
    logger = setup_logging("labcore.test.badlevel", level="LOUD")
    assert logger.level == logging.INFO


def test_add_common_args_defaults():
    args = add_common_args(argparse.ArgumentParser()).parse_args([])
    assert args.theme == "light"
    assert args.scale == 1.0
    assert args.font == "Arial"
    assert args.outdir == "outputs"
    assert args.log_level == "INFO"


def test_add_common_args_parses_overrides():
    parser = add_common_args(argparse.ArgumentParser())
    args = parser.parse_args(["--theme", "dark", "--scale", "1.3", "--outdir", "/tmp/x"])
    assert (args.theme, args.scale, args.outdir) == ("dark", 1.3, "/tmp/x")
