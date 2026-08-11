"""The `labdocs` console script: render, lint, graph, api, audit, schema.

Exit codes are the contract the prek hooks depend on: 0 clean, 1 findings or a
stale generated file, 2 a usage error. `render --check` and `lint` are both wired
into `.pre-commit-config.yaml`, so anything that exits non-zero for a reason
other than a real finding blocks every commit in every consuming repo.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
from importlib import import_module
from pathlib import Path

from labcore.cli import setup_logging
from labcore.labdocs.api import write_api_index
from labcore.labdocs.audit import audit_repos, render_audit

CLEAN, FINDINGS, USAGE = 0, 1, 2

log = setup_logging("labdocs")


def main(argv: list[str] | None = None) -> int:
    """Run one labdocs subcommand.

    Args:
        argv: Argument list without the program name; ``sys.argv[1:]`` when None.

    Returns:
        0 when clean, 1 when there are findings or a generated file is stale,
        2 on a usage error.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit as exc:
        return USAGE if exc.code else CLEAN

    handlers = {
        "render": _cmd_render,
        "lint": _cmd_lint,
        "graph": _cmd_graph,
        "api": _cmd_api,
        "audit": _cmd_audit,
        "schema": _cmd_schema,
    }
    try:
        return handlers[args.command](args)
    except _BackendError as exc:
        log.error("%s", exc)
        return USAGE


class _BackendError(RuntimeError):
    """A sibling labdocs module does not expose the entry point this CLI needs."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="labdocs", description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="command", required=True)

    render = subs.add_parser("render", help="regenerate the manifest, HTML report and codebase map")
    render.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 listing files that regeneration would change",
    )
    _add_root(render)

    lint = subs.add_parser("lint", help="report metadata and naming violations")
    lint.add_argument("--naming", action="store_true", help="also enforce the file-naming regime")
    _add_root(lint)

    _add_root(subs.add_parser("graph", help="print the Mermaid dataflow DAG"))
    _add_root(subs.add_parser("api", help="write knowledge/api_index.md"))

    audit = subs.add_parser("audit", help="report template drift across repos")
    audit.add_argument("paths", nargs="+", type=Path, help="directories to search, depth 2")

    schema = subs.add_parser("schema", help="dump the codebase-meta JSON Schema")
    schema.add_argument("--print", dest="print_", action="store_true", required=True)
    return parser


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root (default cwd)")


def _backend(module_name: str, *candidates: str):
    """Fetch the first available entry point from a sibling labdocs module."""
    try:
        module = import_module(f"labcore.labdocs.{module_name}")
    except ImportError as exc:
        raise _BackendError(f"labdocs {module_name} is unavailable: {exc}") from exc
    for name in candidates:
        found = getattr(module, name, None)
        if callable(found):
            return found
    raise _BackendError(f"{module_name}.py exposes none of: {', '.join(candidates)}")


def _cmd_render(args: argparse.Namespace) -> int:
    if args.check:
        stale = list(_backend("render", "check_stale", "stale_outputs")(args.root))
        for path in stale:
            print(f"stale: {path}")
        return FINDINGS if stale else CLEAN
    for path in _backend("render", "render", "render_all")(args.root):
        log.info("wrote %s", path)
    return CLEAN


def _cmd_lint(args: argparse.Namespace) -> int:
    backend = _backend("lint", "lint_project", "lint_tree", "lint")
    # The naming regime is unconditional in the current linter (LD003), so the
    # flag is only forwarded to a backend that actually takes it.
    accepts_naming = "naming" in inspect.signature(backend).parameters
    findings = backend(args.root, naming=args.naming) if accepts_naming else backend(args.root)
    lines = sorted(f"{f.path}:{f.line}: {f.code} {f.message}" for f in findings)
    for line in lines:
        print(line)
    errors = [f for f in findings if getattr(f, "severity", "error") == "error"]
    return FINDINGS if errors else CLEAN


def _cmd_graph(args: argparse.Namespace) -> int:
    print(_backend("graph", "build_graph", "mermaid_graph", "graph")(args.root))
    return CLEAN


def _cmd_api(args: argparse.Namespace) -> int:
    log.info("wrote %s", write_api_index(args.root))
    return CLEAN


def _cmd_audit(args: argparse.Namespace) -> int:
    print(render_audit(audit_repos(args.paths)))
    return CLEAN


def _cmd_schema(_args: argparse.Namespace) -> int:
    from labcore.meta import load_schema

    print(json.dumps(load_schema(), indent=2))
    return CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
