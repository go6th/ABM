"""Archives: zip, tar (any compression tarfile reads) and gzip, unpacked member by member.

Safety, enforced before and while reading every member:

* path traversal: absolute names, ``..`` parts, drive letters and backslash tricks are blocked;
* links and devices in tar files are skipped (never followed);
* zip bombs: a member whose declared ratio (uncompressed / compressed) exceeds ``max_ratio`` is
  blocked; the bytes actually read are counted and parsing stops at ``max_total_uncompressed_bytes``
  whatever the headers claim; at most ``max_members`` members; nested archives up to ``max_depth``.

Members are never written to disk: each is parsed from its stream by the plugin for its type and
its units are tagged with the member path. The cursor is the member index plus the member
parser's own cursor.

GUARANTEE(test_zip_bomb_and_path_traversal_are_blocked)
"""

from __future__ import annotations

import gzip
import io
import posixpath
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import IO, Any, cast

from research_files.checks import Finding
from research_files.detect import detect
from research_files.parsers.base import ParseContext, Parser, Unit, fatal


class ArchiveLimit(RuntimeError):
    """The archive exceeded a safety limit while being read."""


class CountingReader(io.RawIOBase):
    """Counts the bytes read through it and stops at a budget shared by all members."""

    def __init__(self, inner: IO[bytes], budget: list[int], member_cap: int) -> None:
        self.inner = inner
        self.budget = budget
        self.left = member_cap
        self.position = 0
        self.high_water = 0  # bytes past this were never read: only those are charged

    def readable(self) -> bool:
        """Always readable."""
        return True

    def seekable(self) -> bool:
        """Seekable when the inner stream is."""
        return bool(getattr(self.inner, "seekable", lambda: False)())

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """Delegate (re-reading earlier bytes is not charged again)."""
        self.position = int(self.inner.seek(offset, whence))
        return self.position

    def tell(self) -> int:
        """Delegate."""
        return int(self.inner.tell())

    def readinto(self, buffer: Any) -> int:
        """Read and charge the budget."""
        data = self.inner.read(len(buffer))
        self.position += len(data)
        fresh = max(0, self.position - self.high_water)
        self.high_water = max(self.high_water, self.position)
        self.budget[0] -= fresh
        self.left -= fresh
        if self.budget[0] < 0:
            raise ArchiveLimit("the archive expands beyond the uncompressed size limit")
        if self.left < 0:
            raise ArchiveLimit("a member expands beyond its compressed size times the ratio limit")
        buffer[: len(data)] = data
        return len(data)


def unsafe_name(name: str) -> str | None:
    """Why a member name is unsafe, or None."""
    if not name or "\x00" in name:
        return "empty or NUL in the name"
    if name.startswith(("/", "\\")) or (len(name) > 1 and name[1] == ":"):
        return "absolute path"
    norm = posixpath.normpath(name.replace("\\", "/"))
    if norm.startswith("../") or norm == ".." or "/../" in f"/{norm}/":
        return "path traversal"
    return None


def _blocked(member: str, check: str, message: str, fix: str) -> Finding:
    return Finding("error", check, {"member": member}, message, fix)


class ArchiveParser:
    """See the module docstring. ``parser_for(kind)`` returns the plugin for a member's type."""

    def __init__(self, kind: str, parser_for: Callable[[str], Parser | None]) -> None:
        self.kind = kind
        self.parser_for = parser_for

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """See the module docstring."""
        if cursor.get("done"):
            return
        limits = ctx.cfg.archives
        if ctx.depth > limits.max_depth:
            yield Unit(
                "note",
                {"archive": ctx.name},
                "",
                {"done": True},
                [
                    Finding(
                        "warn",
                        "archive_too_deep",
                        {"archive": ctx.name},
                        f"A nested archive deeper than {limits.max_depth} levels is stored but not"
                        " unpacked.",
                        "Unpack the inner archive and upload its files directly.",
                    )
                ],
            )
            return
        budget = [int(cursor.get("budget", limits.max_total_uncompressed_bytes))]
        try:
            members = list(self._members(stream))
        except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as error:
            yield fatal(
                "note",
                {"archive": ctx.name},
                "corrupt_file",
                f"The archive cannot be read ({type(error).__name__}); it may be truncated.",
                "Create the archive again and upload it.",
            )
            return
        if len(members) > limits.max_members:
            yield Unit(
                "note",
                {"archive": ctx.name},
                "",
                {"member": 0},
                [
                    Finding(
                        "warn",
                        "archive_too_many_members",
                        {"archive": ctx.name},
                        f"The archive has {len(members)} members; only the first"
                        f" {limits.max_members} are read.",
                        "Split the archive into smaller ones.",
                    )
                ],
            )
            members = members[: limits.max_members]
        start = int(cursor.get("member", 0))
        inner_cursor: dict[str, Any] = dict(cursor.get("inner") or {})
        for index in range(start, len(members)):
            name, opener, declared, compressed, problem = members[index]
            location = {"member": name}
            if problem is not None:
                yield Unit(
                    "note",
                    location,
                    "",
                    {"member": index + 1, "budget": budget[0]},
                    [_blocked(name, problem[0], problem[1], problem[2])],
                )
                continue
            ratio = declared / max(1, compressed) if compressed else 1.0
            if compressed and ratio > limits.max_ratio:
                yield Unit(
                    "note",
                    location,
                    "",
                    {"member": index + 1, "budget": budget[0]},
                    [
                        _blocked(
                            name,
                            "zip_bomb_blocked",
                            f"The member expands {ratio:.0f} times (limit {limits.max_ratio:.0f});"
                            " it was not unpacked.",
                            "Remove the member, or upload its content directly.",
                        )
                    ],
                )
                continue
            if declared > budget[0]:
                yield fatal(
                    "note",
                    location,
                    "archive_too_large",
                    "The archive expands beyond the uncompressed size limit; reading stopped here.",
                    "Split the archive or upload its files directly.",
                )
                return
            try:
                handle = opener()
                head = handle.read(8192)
                handle.close()
                kind = detect(io.BytesIO(head), name)
                parser = self.parser_for(kind)
                member_ctx = replace(ctx, name=name, depth=ctx.depth + 1)
                if parser is None:
                    yield Unit(
                        "note",
                        location,
                        "",
                        {"member": index + 1, "budget": budget[0]},
                        [
                            Finding(
                                "info",
                                "unsupported_member",
                                location,
                                f'The member "{name}" ({kind}) is listed but its type is not'
                                " parsed.",
                                None,
                            )
                        ],
                    )
                    continue
                raw = opener()
                # a member may expand to its declared size, or to its compressed size times the
                # ratio limit when nothing is declared (a single .gz); never past the budget
                cap = max(declared, int(compressed * limits.max_ratio)) if compressed else declared
                if not cap:
                    cap = int(max(1, ctx_size(stream)) * limits.max_ratio)
                reader = io.BufferedReader(CountingReader(raw, budget, cap), buffer_size=1 << 20)
                for unit in parser.units(reader, inner_cursor, member_ctx):
                    unit.location = {"member": name, **unit.location}
                    unit.table = f"{name}:{unit.table}" if unit.table else None
                    for i, f in enumerate(unit.findings):
                        unit.findings[i] = replace(f, location={"member": name, **f.location})
                    member_done = bool(unit.cursor.get("done"))
                    unit.cursor = (
                        {"member": index + 1, "budget": budget[0]}
                        if member_done
                        else {"member": index, "inner": unit.cursor, "budget": budget[0]}
                    )
                    if unit.fatal:
                        unit.fatal = False  # a broken member does not stop the archive
                        unit.cursor = {"member": index + 1, "budget": budget[0]}
                        yield unit
                        break
                    yield unit
                raw.close()
            except ArchiveLimit as limit:
                yield fatal(
                    "note",
                    location,
                    "zip_bomb_blocked",
                    f"Reading stopped: {limit}. The archive expands far beyond its declared size.",
                    "Do not upload this archive; it looks like a decompression bomb.",
                )
                return
            except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as error:
                yield Unit(
                    "note",
                    location,
                    "",
                    {"member": index + 1, "budget": budget[0]},
                    [
                        _blocked(
                            name,
                            "corrupt_member",
                            f'The member "{name}" cannot be read ({type(error).__name__}).',
                            "Create the archive again and upload it.",
                        )
                    ],
                )
            inner_cursor = {}
        yield Unit(
            "note", {"archive": ctx.name}, "", {"done": True}, stats={"members": len(members)}
        )

    Member = tuple[str, Callable[[], IO[bytes]], int, int, tuple[str, str, str] | None]

    def _members(self, stream: IO[bytes]) -> Iterator[Member]:
        stream.seek(0)
        if self.kind == "zip":
            archive = zipfile.ZipFile(stream)  # noqa: SIM115 - members stream from it lazily
            for info in archive.infolist():
                if info.is_dir():
                    continue
                problem = unsafe_name(info.filename)

                def zip_opener(i: zipfile.ZipInfo = info) -> IO[bytes]:
                    return archive.open(i)

                yield (
                    info.filename,
                    zip_opener,
                    info.file_size,
                    info.compress_size,
                    (
                        "path_traversal_blocked",
                        f'The member name "{info.filename[:120]}" is unsafe ({problem}); it was'
                        " not unpacked.",
                        "Rebuild the archive with relative paths only.",
                    )
                    if problem
                    else None,
                )
            return
        if self.kind == "gz":
            head = gzip.GzipFile(fileobj=stream).read(512)
            stream.seek(0)
            if len(head) > 262 and head[257:262] == b"ustar":
                yield from self._tar(tarfile.open(fileobj=stream, mode="r:gz"))  # noqa: SIM115
                return

            def gz_opener() -> IO[bytes]:
                return cast(IO[bytes], gzip.GzipFile(fileobj=_fresh(stream)))

            yield ("content", gz_opener, 0, 0, None)
            return
        yield from self._tar(tarfile.open(fileobj=stream, mode="r:*"))  # noqa: SIM115

    def _tar(self, archive: tarfile.TarFile) -> Iterator[Member]:
        for info in archive:
            if info.isdir():
                continue
            problem = unsafe_name(info.name)
            reason = None
            if problem:
                reason = (
                    "path_traversal_blocked",
                    f'The member name "{info.name[:120]}" is unsafe ({problem}); it was not'
                    " unpacked.",
                    "Rebuild the archive with relative paths only.",
                )
            elif info.issym() or info.islnk() or not info.isfile():
                reason = (
                    "link_or_device_skipped",
                    f'The member "{info.name[:120]}" is a link or a device; links are never'
                    " followed.",
                    "Replace links with the files they point to.",
                )

            def opener(i: tarfile.TarInfo = info) -> IO[bytes]:
                handle = archive.extractfile(i)
                if handle is None:
                    raise tarfile.TarError("not a regular file")
                return handle

            yield (info.name, opener, info.size, 0, reason)


def ctx_size(stream: IO[bytes]) -> int:
    """The size of a seekable stream (its position is restored)."""
    here = stream.tell()
    stream.seek(0, io.SEEK_END)
    size = stream.tell()
    stream.seek(here)
    return size


def _fresh(stream: IO[bytes]) -> IO[bytes]:
    stream.seek(0)
    return stream
