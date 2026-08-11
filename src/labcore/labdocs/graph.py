"""Two renderings of one edge list: a Mermaid DAG and inline HTML nodes.

The HTML rendering exists so `explain_codebase.html` draws its dataflow with no
mermaid runtime — the report is one self-contained file and may never fetch a
script. Mermaid is emitted alongside it for tools that speak it (GitHub, the
`labdocs graph` command). Both come from the same `next:` edges, so they cannot
disagree.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoids a cycle: render imports graph, never the reverse
    from labcore.labdocs.render import Project

# Reference layout: four nodes across, then one per row in the last column.
ROW_COLUMNS = 4

LANGUAGES = {
    ".py": "python",
    ".r": "r",
    ".R": "r",
    ".sh": "bash",
    ".bash": "bash",
    ".nf": "nextflow",
    ".rs": "rust",
    ".jl": "julia",
    ".pl": "perl",
}

# Okabe-Ito. Mermaid needs literal hex; the HTML legend uses the CSS variables so
# it follows the theme toggle instead of freezing at the light-theme values.
CLASS_STROKE = {"entry": "#E69F00", "step": "#0072B2", "terminal": "#009E73"}
CLASS_VAR = {"entry": "var(--c2)", "step": "var(--c1)", "terminal": "var(--c3)"}
NODE_CLASS = {"entry": "node src", "step": "node", "terminal": "node term"}


def language_of(path) -> str:
    """Name the host language of a script from its extension.

    Args:
        path: Script path.

    Returns:
        Lowercase language name, or the bare suffix when unknown.
    """
    return LANGUAGES.get(path.suffix, path.suffix.lstrip(".") or "?")


def edges(project: Project) -> list[tuple[str, str]]:
    """List the script-to-script dataflow edges.

    Args:
        project: Loaded project.

    Returns:
        (producer, consumer) name pairs in run order.
    """
    known = {step.name for step in project.steps}
    return [
        (step.name, nxt)
        for step in project.steps
        for nxt in step.next_steps
        if nxt in known  # a dangling target is `labdocs lint`'s finding, not a drawn edge
    ]


def classify(project: Project) -> dict[str, str]:
    """Label every step entry / step / terminal for colour coding.

    Args:
        project: Loaded project.

    Returns:
        Step name -> one of "entry", "step", "terminal".
    """
    pairs = edges(project)
    incoming = {consumer for _, consumer in pairs}
    outgoing = {producer for producer, _ in pairs}
    kinds = {}
    for step in project.steps:
        if step.is_final or step.name not in outgoing:
            kinds[step.name] = "terminal"
        else:
            kinds[step.name] = "entry" if step.name not in incoming else "step"
    return kinds


def mermaid_dag(project: Project) -> str:
    """Render the dataflow as a Mermaid flowchart.

    Args:
        project: Loaded project.

    Returns:
        Mermaid source, without fences.
    """
    kinds = classify(project)
    lines = ["flowchart LR"]
    for step in project.steps:
        label = f"{step.name}<br/>{language_of(step.path)} · {step.order or 0:02d}"
        lines.append(f'    {step.name}["{label}"]:::{kinds[step.name]}')
    lines += [f"    {producer} --> {consumer}" for producer, consumer in edges(project)]
    lines += [
        f"    classDef {kind} stroke:{colour},stroke-width:2px,fill:none;"
        for kind, colour in CLASS_STROKE.items()
    ]
    return "\n".join(lines)


def build_graph(root: Path) -> str:
    """Load a project from disk and render its Mermaid DAG.

    The entry point `labdocs graph` calls. The import is function-local because
    render imports this module — at module level it would be a cycle.

    Args:
        root: Project root.

    Returns:
        Mermaid source, without fences.
    """
    from labcore.labdocs.render import load_project

    return mermaid_dag(load_project(root))


def _node_html(step, kind: str) -> str:
    """One `.node` div carrying name, language and order."""
    tail = " · final" if kind == "terminal" else ""
    small = f"{language_of(step.path)} · {step.order or 0:02d}{tail}"
    label = f"{escape(step.name)}<small>{escape(small)}</small>"
    return f'<div class="{NODE_CLASS[kind]}">{label}</div>'


def _spacers(n: int) -> str:
    """Hidden node/arrow pairs that align a continuation row under the last column."""
    cell = '<div class="node" style="visibility:hidden">spacer</div>'
    link = '<div class="arrow" style="visibility:hidden"></div>'
    return (cell + link) * n


def dag_html(project: Project) -> str:
    """Render the dataflow as the reference's `.dag` / `.dagrow` / `.node` markup.

    The layout is linear in run order: exact topology lives in the Mermaid
    rendering, which is emitted from the same edges beside it.

    Args:
        project: Loaded project.

    Returns:
        A complete `<div class="dag">` block including the legend.
    """
    kinds = classify(project)
    head = project.steps[:ROW_COLUMNS]
    arrow = '<div class="arrow"></div>'
    rows = [arrow.join(_node_html(s, kinds[s.name]) for s in head)] if head else []
    rows += [
        _spacers(ROW_COLUMNS - 1) + _node_html(step, kinds[step.name])
        for step in project.steps[ROW_COLUMNS:]
    ]
    body = "\n".join(f'      <div class="dagrow">{row}</div>' for row in rows)
    legend = "\n".join(
        f'    <span><i class="sw" style="border-color:{CLASS_VAR[kind]}"></i> {label}</span>'
        for kind, label in (
            ("entry", "entry point"),
            ("step", "intermediate step"),
            ("terminal", "terminal / deliverable"),
        )
    )
    return (
        '<div class="dag">\n    <div class="stack">\n'
        f"{body}\n    </div>\n"
        f'  <div class="legend">\n{legend}\n  </div>\n</div>'
    )
