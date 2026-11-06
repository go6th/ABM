"""``config/`` holds only files that ``src/`` loads (ADR-0136). Registers that no code reads
(claims, messaging rules, pricing, the risk register, SOC 2 controls, secret rotation, retention,
the minimisation map, the data-model spec, the billing proposal) live in ``docs/registers/``.

The check: every file at the top level of ``config/`` is named literally somewhere in ``src/``
(a loader's ``CONFIG_DIR / "<name>"``, a typed config module). Sub-folders (``skill/``,
``parity/``, ``telemetry/``, ``hermetic/``) are loaded by their packages through the folder
name and are checked by their own tests.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
SRC = ROOT / "src"

# a file no loader names, with the reason it may stay
EXCEPTIONS: dict[str, str] = {
    "README.md": "the folder's index for people; no code reads it",
}
SUBFOLDERS = {"skill", "parity", "telemetry", "hermetic"}


def _src_text() -> str:
    return "\n".join(p.read_text() for p in SRC.rglob("*.py"))


def test_every_config_file_is_loaded() -> None:
    text = _src_text()
    unread = sorted(
        p.name
        for p in CONFIG.iterdir()
        if p.is_file() and p.name not in EXCEPTIONS and p.name not in text
    )
    assert not unread, (
        "config/ files no code under src/ names; move them to docs/registers/ (a register) or"
        f" unwanted-things/ (dead), or add a reasoned exception: {unread}"
    )


def test_the_subfolders_are_named_by_src_too() -> None:
    text = _src_text()
    folders = {p.name for p in CONFIG.iterdir() if p.is_dir() and not p.name.startswith(".")}
    assert folders <= SUBFOLDERS | {"__pycache__"}, sorted(folders - SUBFOLDERS)
    for name in SUBFOLDERS & folders:
        assert f'"{name}"' in text or f"{name}/" in text, name


def test_the_exceptions_still_exist() -> None:
    for name in EXCEPTIONS:
        assert (CONFIG / name).exists(), f"drop the stale exception {name}"
