"""Component inventory for the `pipeline` flavour, read from nf-core meta.yml.

nf-core `meta.yml` carries no `order:` and no `next:`, so a pipeline report gets
one row per module or subworkflow and nothing that pretends to know run order —
Nextflow decides that at runtime (build plan §5.3). The sidecars are parsed
as-is; no second metadata carrier is introduced for pipelines.
"""

from __future__ import annotations

import re
from html import escape
from pathlib import Path

import yaml

INVENTORY_COLUMNS = ("Component", "Path", "Tool + version", "Inputs", "Outputs", "Container")

COMPONENT_DIRS = ("modules", "subworkflows")


def _channel_names(spec) -> list[str]:
    """Flatten a meta.yml input:/output: section to channel names.

    nf-core has shipped these as a mapping, a list of mappings, and a list of
    lists of mappings across template generations; all three land here.
    """
    out: list[str] = []
    if isinstance(spec, dict):
        out.extend(str(key) for key in spec)
    elif isinstance(spec, list):
        for item in spec:
            out.extend(_channel_names(item))
    elif spec is not None:
        out.append(str(spec))
    return out


def _tool_labels(data: dict) -> str:
    """Join a meta.yml tools: section into 'name version' labels."""
    labels = []
    for entry in data.get("tools") or []:
        for name, body in (entry or {}).items():
            ver = (body or {}).get("version") if isinstance(body, dict) else None
            labels.append(f"{name} {ver}" if ver else str(name))
    return ", ".join(labels)


def _container(module_dir: Path) -> str:
    """Read the container reference out of a module's main.nf.

    The last match wins: nf-core modules declare the singularity image first and
    the docker one second, and the docker reference is what most readers want.
    """
    main = module_dir / "main.nf"
    if not main.is_file():
        return ""
    found = re.findall(r"container\s+[\"']([^\"']+)[\"']", main.read_text(encoding="utf-8"))
    return found[-1] if found else ""


def components(root: Path) -> list[dict]:
    """Inventory every module and subworkflow carrying a meta.yml sidecar.

    Args:
        root: Pipeline project root.

    Returns:
        One dict per component: name, path, tools, inputs, outputs, container.
    """
    out = []
    for base in COMPONENT_DIRS:
        directory = root / base
        if not directory.is_dir():
            continue
        for meta in sorted(directory.rglob("meta.yml")):
            data = yaml.safe_load(meta.read_text(encoding="utf-8")) or {}
            out.append(
                {
                    "name": data.get("name", meta.parent.name),
                    "path": meta.parent.relative_to(root).as_posix(),
                    "tools": _tool_labels(data),
                    "inputs": _channel_names(data.get("input")),
                    "outputs": _channel_names(data.get("output")),
                    "container": _container(meta.parent),
                }
            )
    return out


def html_row(component: dict) -> str:
    """Render one component as an HTML table row.

    Args:
        component: One entry from `components`.

    Returns:
        A `<tr>` matching INVENTORY_COLUMNS.
    """
    cells = [
        f"<strong>{escape(component['name'])}</strong>",
        f"<code>{escape(component['path'])}</code>",
        escape(component["tools"]) or "—",
        "<br>".join(f"<code>{escape(n)}</code>" for n in component["inputs"]) or "—",
        "<br>".join(f"<code>{escape(n)}</code>" for n in component["outputs"]) or "—",
        f"<code>{escape(component['container'])}</code>" if component["container"] else "—",
    ]
    return "    <tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def md_row(component: dict) -> str:
    """Render one component as a Markdown table row.

    Args:
        component: One entry from `components`.

    Returns:
        A pipe-delimited row matching INVENTORY_COLUMNS.
    """
    joined = [" · ".join(f"`{n}`" for n in component[key]) or "—" for key in ("inputs", "outputs")]
    container = f"`{component['container']}`" if component["container"] else "—"
    return (
        f"| **{component['name']}** | `{component['path']}` | {component['tools'] or '—'} | "
        f"{joined[0]} | {joined[1]} | {container} |"
    )
