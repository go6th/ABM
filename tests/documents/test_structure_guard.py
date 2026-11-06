"""The structure guard in front of the malware scanner (PR4 of the upload hardening, 2026-11-04).

Stock ClamAV passes Office macros; the guard refuses them, and executables, in every environment,
so a file accepted locally is accepted in the cloud. It never judges a name: a ``.py`` or ``.js``
file is text and reaches the scanner. An unavailable scanner stays unavailable behind it.

GUARANTEE(test_the_guard_refuses_macros_and_executables_and_passes_scripts_as_text)
"""

from __future__ import annotations

import io
import zipfile

import pytest

from documents.scan import StructureGuard, scanner_for
from tests.documents import fixtures
from tests.fakes.scanner import DownScanner, FakeClamd

pytestmark = pytest.mark.asyncio


def _zip(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return out.getvalue()


async def _verdict(data: bytes, inner: object | None = None) -> tuple[str, str | None]:
    guard = StructureGuard(inner or FakeClamd())  # type: ignore[arg-type]
    got = await guard.scan(io.BytesIO(data))
    return got.result, got.signature


async def test_the_guard_refuses_macros_and_executables_and_passes_scripts_as_text() -> None:
    assert await _verdict(fixtures.macro_docx()) == ("infected", "office-macro")
    assert await _verdict(b"MZ\x90\x00" + b"\x00" * 64) == ("infected", "executable")
    inside = _zip({"tools/run.exe": b"MZ\x90\x00" + b"\x00" * 64, "notes.md": b"# hi"})
    assert await _verdict(inside) == ("infected", "archive-executable")
    scripts = _zip({"build/scan.py": b"print('hi')\n", "build/app.js": b"console.log(1)\n"})
    assert await _verdict(scripts) == ("clean", None)
    assert await _verdict(b"import os\nprint(os.name)\n") == ("clean", None)
    # the scanner's own verdict stands behind the guard
    assert (await _verdict(fixtures.eicar_txt()))[0] == "infected"
    assert await _verdict(b"plain text", DownScanner()) == ("unavailable", None)


def test_every_configured_scanner_sits_behind_the_guard() -> None:
    for env in ("dev", "test", "staging", "prod"):
        assert isinstance(scanner_for(env, "clamd", "clamav", 3310), StructureGuard)
        assert isinstance(scanner_for(env, "clamd", None, 3310), StructureGuard)
