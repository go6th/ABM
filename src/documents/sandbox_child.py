"""The parser sandbox's child process: lock down, parse one document, print units as JSON lines.

Run only by ``documents.sandbox`` as ``python -I -m documents.sandbox_child <path> <type> <name>
<memory MB> <CPU s> <part>`` with an empty environment. ``part`` is JSON: ``{"plan": {...}}`` prints
the document's parts (``documents.parts``) as one line; otherwise ``{"cursor", "end_offset",
"end_page"}`` parses one part: from the cursor, through a reader that ends at the part's end
offset, up to its last page. Before any parser module is imported, it

1. sets resource limits: address space, CPU seconds, no file creation (``RLIMIT_FSIZE`` 0);
2. installs an audit hook (which cannot be removed) that refuses every network operation
   (``socket.*``), every new process (``subprocess``, ``os.system``, ``os.exec*``,
   ``os.posix_spawn``, ``os.fork``), loading native libraries by path (``ctypes.dlopen``) and any
   open for writing.

A document that tries any of these makes its parse fail; the content never reaches the network.
The parent reads the units from stdout; stderr carries only a short error class.

GUARANTEE(test_the_parser_sandbox_has_no_network)
"""

from __future__ import annotations

import json
import sys
from typing import Any

_BLOCKED_PREFIXES = ("socket.", "subprocess.", "os.exec", "os.posix_spawn", "os.spawn", "pty.")
_BLOCKED = frozenset({"os.system", "os.fork", "os.forkpty", "ctypes.dlopen", "os.startfile"})


def _audit(event: str, args: tuple[Any, ...]) -> None:
    """Refuse network, processes, native loads and writes (see the module docstring)."""
    if event in _BLOCKED or event.startswith(_BLOCKED_PREFIXES):
        raise PermissionError(f"sandbox refused {event}")
    if event == "open" and len(args) >= 2:
        mode = args[1]
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            raise PermissionError("sandbox refused a write")
        if isinstance(mode, int) and mode & 0o3:  # O_WRONLY or O_RDWR
            raise PermissionError("sandbox refused a write")


def _limits(memory_mb: int, cpu_s: int) -> None:
    import resource

    memory = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def main(argv: list[str]) -> int:
    """Parse ``argv`` = path, type, name, memory MB, CPU seconds, part; print JSON lines."""
    path, kind, name, memory_mb, cpu_s = argv[0], argv[1], argv[2], int(argv[3]), int(argv[4])
    part: dict[str, Any] = json.loads(argv[5]) if len(argv) > 5 else {}
    _limits(memory_mb, cpu_s)
    # import everything the parsers need before the hook: imports read files only, but some
    # modules probe the platform with calls the hook refuses
    from documents import parts
    from research_files.config import load_config
    from research_files.parsers import archives, base, documents, media, registry, tabular

    del archives, documents, media, tabular
    cfg = load_config()
    if "plan" in part:
        with open(path, "rb") as raw:  # noqa: PTH123 - opened before the hook
            sys.addaudithook(_audit)
            planned = parts.plan(
                raw, kind, unit_text_bytes=cfg.parsing.unit_text_bytes, **part["plan"]
            )
        sys.stdout.write(json.dumps({"parts": [p.to_json() for p in planned]}) + "\n")
        return 0
    parser = registry.parser_for(kind)
    out = sys.stdout
    if parser is None:
        out.write(json.dumps({"fatal": True, "check": "unsupported_type"}) + "\n")
        return 0
    raw = open(path, "rb")  # noqa: SIM115 - held for the parse; opened before the hook
    stream = parts.bounded(raw, part.get("end_offset"))
    end_page = part.get("end_page")
    sys.addaudithook(_audit)
    ctx = base.ParseContext(cfg, name)
    for unit in parser.units(stream, dict(part.get("cursor") or {}), ctx):
        if end_page is not None and int(unit.location.get("page", 0)) > int(end_page):
            break  # the next part's first page
        if end_page is not None and unit.cursor.get("done"):
            continue  # the whole-document note (pages) belongs to no single part
        record: dict[str, Any] = {
            "kind": unit.kind,
            "location": unit.location,
            "text": unit.text,
            "heading": unit.heading,
            "fatal": unit.fatal,
            "findings": [
                {"severity": f.severity, "check": f.check, "message": f.message}
                for f in unit.findings
            ],
        }
        if unit.rows is not None:
            record["header"] = [str(h) for h in (unit.header or [])]
            record["rows"] = [[n, [_cell(c) for c in cells]] for n, cells in unit.rows]
        if unit.table is not None:
            record["table"] = unit.table
        out.write(json.dumps(record, default=str) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    try:
        CODE = main(sys.argv[1:])
    except Exception as error:  # noqa: BLE001 - the class name is the only thing reported
        sys.stderr.write(type(error).__name__[:80])
        CODE = 3
    sys.stdout.flush()
    sys.exit(CODE)
