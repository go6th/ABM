"""The parser sandbox's child process: lock down, parse one document, print units as JSON lines.

Run only by ``documents.sandbox`` as ``python -I -m documents.sandbox_child <path> <type> <name>
<memory MB> <CPU s> <part>`` with an empty environment. ``part`` is JSON: ``{"plan": {...}}`` prints
the document's parts (``documents.parts``) as one line; otherwise ``{"cursor", "end_offset",
"end_page"}`` parses one part: from the cursor, through a reader that ends at the part's end
offset, up to its last page. Before any parser module is imported, it

1. sets resource limits: address space, CPU seconds, no file creation (``RLIMIT_FSIZE`` 0);
2. once the file is open, installs a seccomp filter in the kernel (security audit M2): after
   ``prctl(PR_SET_NO_NEW_PRIVS)``, which needs no privilege and works for a non-root user in a
   container, every thread of the process gets EPERM for ``socket``, ``socketpair``,
   ``connect``, ``bind``, ``listen``, ``accept``, ``execve``, ``execveat``, ``fork``, ``vfork``,
   ``io_uring_setup``, ``ptrace`` and a ``clone`` that is not a thread; ``clone3`` answers
   ENOSYS so the C library falls back to ``clone`` for threads. Native parser code (libxml2,
   Pillow, pyarrow) therefore cannot open a socket, reach the metadata server or start a
   process, whatever Python sees. A foreign system-call ABI (32-bit calls on x86-64) kills the
   process. On Linux a filter that cannot be installed stops the parse (fail closed); other
   systems (a developer's Mac) keep the audit hook only. The filter is raw classic BPF built
   here and installed through the C library with ``ctypes``: no extra library is needed.
   VERIFY(V-SANDBOX-1): Cloud Run's sandbox accepts ``seccomp(SECCOMP_SET_MODE_FILTER, TSYNC)``;
3. installs an audit hook (which cannot be removed) that refuses every network operation
   (``socket.*``), every new process (``subprocess``, ``os.system``, ``os.exec*``,
   ``os.posix_spawn``, ``os.fork``), loading native libraries by path (``ctypes.dlopen``) and any
   open for writing: the second layer, with clear errors for Python-level attempts.

A document that tries any of these makes its parse fail; the content never reaches the network.
The parent reads the units from stdout; stderr carries only a short error class.

GUARANTEE(test_the_parser_sandbox_has_no_network)
GUARANTEE(test_the_kernel_filter_refuses_a_native_socket_and_a_process_but_not_a_thread)
"""

from __future__ import annotations

import json
import struct
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


# Classic BPF over ``struct seccomp_data`` (nr at 0, arch at 4, args at 16, little-endian).
_LD_ABS, _JEQ, _JGE, _JSET, _RET = 0x20, 0x15, 0x35, 0x45, 0x06
_ALLOW, _ERRNO, _KILL_PROCESS = 0x7FFF0000, 0x00050000, 0x80000000
_EPERM, _ENOSYS, _CLONE_THREAD = 1, 38, 0x00010000
# arch audit id, seccomp syscall, denied calls, clone, clone3 (x86-64 also refuses the x32 ABI)
_ARCH: dict[str, dict[str, Any]] = {
    "x86_64": {
        "audit": 0xC000003E,
        "seccomp": 317,
        # socket connect accept bind listen socketpair fork vfork execve ptrace accept4
        # execveat io_uring_setup
        "deny": (41, 42, 43, 49, 50, 53, 57, 58, 59, 101, 288, 322, 425),
        "clone": 56,
        "x32": 0x40000000,
    },
    "aarch64": {
        "audit": 0xC00000B7,
        "seccomp": 277,
        # socket socketpair bind listen accept connect execve ptrace accept4 execveat
        # io_uring_setup
        "deny": (198, 199, 200, 201, 202, 203, 221, 117, 242, 281, 425),
        "clone": 220,
        "x32": None,
    },
}
_CLONE3 = 435


class SandboxUnavailable(RuntimeError):
    """The kernel filter could not be installed (the parse must not run)."""


def _filter(arch: dict[str, Any]) -> bytes:
    """The seccomp program for one architecture (see the module docstring)."""
    ins: list[tuple[int, int, int, int]] = [
        (_LD_ABS, 0, 0, 4),
        (_JEQ, 1, 0, arch["audit"]),
        (_RET, 0, 0, _KILL_PROCESS),
        (_LD_ABS, 0, 0, 0),
    ]
    if arch["x32"] is not None:
        ins += [(_JGE, 0, 1, arch["x32"]), (_RET, 0, 0, _ERRNO | _EPERM)]
    for nr in arch["deny"]:
        ins += [(_JEQ, 0, 1, nr), (_RET, 0, 0, _ERRNO | _EPERM)]
    ins += [
        (_JEQ, 0, 4, arch["clone"]),
        (_LD_ABS, 0, 0, 16),  # clone's flags (low word of the first argument)
        (_JSET, 1, 0, _CLONE_THREAD),
        (_RET, 0, 0, _ERRNO | _EPERM),
        (_RET, 0, 0, _ALLOW),
        (_JEQ, 0, 1, _CLONE3),
        (_RET, 0, 0, _ERRNO | _ENOSYS),
        (_RET, 0, 0, _ALLOW),
    ]
    return b"".join(struct.pack("=HBBI", *i) for i in ins)


def deny_network_and_processes() -> bool:
    """Install the kernel filter on every thread; False where seccomp does not exist (not
    Linux). Raises ``SandboxUnavailable`` on Linux when it cannot be installed."""
    import ctypes
    import platform

    if sys.platform != "linux":
        return False
    arch = _ARCH.get(platform.machine())
    if arch is None:
        raise SandboxUnavailable(f"no seccomp filter for {platform.machine()}")
    program = _filter(arch)

    class _Prog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_char_p)]

    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise SandboxUnavailable(f"no_new_privs refused (errno {ctypes.get_errno()})")
    prog = _Prog(len(program) // 8, program)
    # SECCOMP_SET_MODE_FILTER (1) with SECCOMP_FILTER_FLAG_TSYNC (1): every thread, or none
    done = libc.syscall(arch["seccomp"], 1, 1, ctypes.byref(prog))
    if done != 0:
        raise SandboxUnavailable(f"seccomp refused (result {done}, errno {ctypes.get_errno()})")
    return True


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
            deny_network_and_processes()
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
    cursor = dict(part.get("cursor") or {})
    end_member = cursor.pop("end_member", None)  # a zip part: the next part's first member
    deny_network_and_processes()
    sys.addaudithook(_audit)
    ctx = base.ParseContext(cfg, name)
    last = False
    for unit in parser.units(stream, cursor, ctx):
        if end_page is not None and int(unit.location.get("page", 0)) > int(end_page):
            break  # the next part's first page
        if (end_page is not None or end_member is not None) and unit.cursor.get("done"):
            continue  # the whole-document note (pages, members) belongs to no single part
        if end_member is not None and "inner" not in unit.cursor:
            # the unit that closes a member carries the next member's index
            last = int(unit.cursor.get("member", 0)) >= int(end_member)
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
        if last:
            break  # this part's last member is read; the next part starts at end_member
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
