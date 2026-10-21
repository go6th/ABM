"""The Word renderer subprocess (D-26, B15, spec 7).

One call per document: the validated ``DocumentSpec`` JSON goes to stdin, docx bytes come back
on stdout. The process runs:

* with a CLEARED environment (only a fixed locale): no database URL, credentials, tokens or
  cloud settings can reach it (``clean_env``, tested);
* in its own process group with a timeout; on expiry the whole group is killed;
* with stdout and stderr read in bounded chunks: more than the cap kills the process
  (``output too large``), so a runaway renderer cannot exhaust memory;
* at most ``reruns`` (1) re-run after a failure, then ``RenderError``. The render is
  deterministic, so a re-run only helps a transient failure.

Output must be a zip holding ``word/document.xml``; anything else is ``malformed output``.
GUARANTEE(test_renderer_environment_is_cleared)
GUARANTEE(test_renderer_failures_are_typed)
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import signal
import subprocess  # nosec B404 - fixed argv, no shell, cleared environment
import sys
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from exports.config import RendererLimits
from exports.errors import NotConfigured, RenderError
from exports.word.contract import DocumentSpec

NODE_LIBRARY = Path(__file__).resolve().parent / "node"
_CHUNK = 65536


def clean_env() -> dict[str, str]:
    """The renderer's whole environment: a fixed locale and nothing else."""
    return {"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}


@dataclass(frozen=True)
class RendererCommand:
    """The argv of a renderer (no shell)."""

    name: str
    argv: tuple[str, ...]


def python_renderer() -> RendererCommand:
    """The interim python-docx renderer (``python -I -m exports.word.render_cli``)."""
    return RendererCommand(
        "python-docx-interim", (sys.executable, "-I", "-m", "exports.word.render_cli")
    )


def node_renderer(library: Path = NODE_LIBRARY) -> RendererCommand:
    """The ported Node library (``render.js`` behind the same contract). Raises
    ``NotConfigured`` until the legacy library has been supplied and ported."""
    entry = library / "render.js"
    node = shutil.which("node")
    if node is None or not entry.is_file():
        raise NotConfigured("the Node docx library is not ported yet (legacy files not supplied)")
    return RendererCommand("node-docx", (node, str(entry)))


def _drain(
    stream: IO[bytes],
    cap: int,
    sink: list[bytes],
    over: threading.Event,
    proc: subprocess.Popen[bytes],
) -> None:
    total = 0
    # LOOP(renderer output drain): cap=the byte cap (max_output_bytes or max_stderr_bytes) in
    # 64 KiB reads; exit=end of stream, cap exceeded (the process is killed)
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            return
        total += len(chunk)
        if total > cap:
            over.set()
            _signal(proc)  # stop it now: a blocked writer would otherwise run to the timeout
            return
        sink.append(chunk)


def _feed(stream: IO[bytes], payload: bytes) -> None:
    with contextlib.suppress(OSError):  # the renderer may exit before reading everything
        stream.write(payload)
    with contextlib.suppress(OSError):
        stream.close()


def _signal(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def _kill(proc: subprocess.Popen[bytes]) -> None:
    _signal(proc)
    proc.wait()


class SubprocessWordRenderer:
    """``WordRenderPort`` over a subprocess (see the module docstring)."""

    def __init__(self, command: RendererCommand, limits: RendererLimits) -> None:
        self.command = command
        self.limits = limits
        self.last_exit: str = "not_run"

    def _once(self, payload: bytes) -> bytes:
        proc = subprocess.Popen(  # nosec B603 - fixed argv from RendererCommand, no shell
            list(self.command.argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env(),
            cwd="/",
            start_new_session=True,
        )
        assert proc.stdin and proc.stdout and proc.stderr
        out: list[bytes] = []
        err: list[bytes] = []
        over_out, over_err = threading.Event(), threading.Event()
        threads = [
            threading.Thread(target=_feed, args=(proc.stdin, payload), daemon=True),
            threading.Thread(
                target=_drain,
                args=(proc.stdout, self.limits.max_output_bytes, out, over_out, proc),
                daemon=True,
            ),
            threading.Thread(
                target=_drain,
                args=(proc.stderr, self.limits.max_stderr_bytes, err, over_err, proc),
                daemon=True,
            ),
        ]
        for t in threads:
            t.start()
        try:
            proc.wait(timeout=self.limits.timeout_s)
        except subprocess.TimeoutExpired:
            _kill(proc)
            raise RenderError("timeout") from None
        finally:
            if over_out.is_set() or over_err.is_set():
                _kill(proc)
            for t in threads:
                t.join(timeout=5)
            if not any(t.is_alive() for t in threads):
                proc.stdout.close()
                proc.stderr.close()
        if over_out.is_set():
            raise RenderError("output too large")
        if over_err.is_set():
            raise RenderError("diagnostics too large")
        if proc.returncode != 0:
            raise RenderError(f"exit {proc.returncode}")
        data = b"".join(out)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist():
                    raise RenderError("malformed output")
        except zipfile.BadZipFile:
            raise RenderError("malformed output") from None
        return data

    def render(self, spec: DocumentSpec) -> bytes:
        """docx bytes for ``spec``; at most ``1 + reruns`` attempts, then ``RenderError``."""
        payload = spec.model_dump_json().encode()
        attempts = 1 + self.limits.reruns
        # LOOP(renderer re-run): cap=1 + reruns (1) attempts per document;
        # exit=converged (rendered), cap_reached (RenderError)
        last: RenderError | None = None
        for _ in range(attempts):
            try:
                data = self._once(payload)
            except RenderError as error:
                last = error
                continue
            self.last_exit = "converged"
            return data
        self.last_exit = "cap_reached"
        assert last is not None
        raise RenderError(f"{last.message} after {attempts} attempt(s)")
