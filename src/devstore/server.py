"""The development object store: the subset of the Google Cloud Storage resumable-upload protocol
the browser uploader uses, on a local directory (owner decision 2026-10-14, Phase 4B section 3).

The browser sends a file straight to storage, never through the web app or the API:

* the API opens a session (``Store.open_session``: object key and size, written into the
  store's directory, which the API shares) and hands the browser the session URL, a 256-bit
  capability like a GCS session URI;
* the browser ``PUT``s chunks to it with ``Content-Range: bytes a-b/total``; each answer is
  ``308`` with ``Range: bytes=0-<committed>`` until the last, which answers ``200``;
* after a reload or a dropped connection, ``PUT`` with ``Content-Range: bytes */total`` and no
  body says how much is committed, and the upload continues from there.

A chunk must start at or before the committed offset (the part already committed is skipped, so
a repeated chunk changes nothing); nothing past the declared size is accepted; a session expires
after ``DEVSTORE_SESSION_TTL_S``. The finished object is renamed into place under
``DEVSTORE_ROOT``, the same directory the API's local blob store reads. Refused outside
``APP_ENV`` dev and test.

GUARANTEE(test_a_dropped_upload_resumes_from_the_committed_offset)
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
QUERY = re.compile(r"^bytes \*/(\d+)$")
SID = re.compile(r"[A-Za-z0-9_-]{40,64}")


class Store:
    """Sessions and objects under one root directory."""

    def __init__(self, root: str | os.PathLike[str], public_url: str, ttl_s: int = 172_800) -> None:
        self.root = Path(root)
        self.sessions = self.root / ".sessions"
        self.sessions.mkdir(parents=True, exist_ok=True)
        self.public_url = public_url.rstrip("/")
        self.ttl_s = ttl_s

    def open_session(self, key: str, size: int) -> str:
        """A new upload session for ``size`` bytes under ``key``; its URL."""
        self.target(key)  # refuses an escaping key
        if size <= 0:
            raise ValueError("an upload has at least one byte")
        sid = secrets.token_urlsafe(32)
        record = {"key": key, "size": size, "expires": time.time() + self.ttl_s}
        (self.sessions / f"{sid}.json").write_text(json.dumps(record))
        return f"{self.public_url}/upload/{sid}"

    def target(self, key: str) -> Path:
        """The object's path (refuses a key that escapes the root)."""
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("key escapes the root")
        return path

    def session(self, sid: str) -> dict[str, Any] | None:
        """A live session, or None."""
        if not SID.fullmatch(sid):
            return None
        path = self.sessions / f"{sid}.json"
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text())
        if data["expires"] < time.time():
            return None
        return data

    def partial(self, sid: str) -> Path:
        """The bytes committed so far."""
        return self.sessions / f"{sid}.part"

    def committed(self, sid: str) -> int:
        """How many bytes are committed."""
        part = self.partial(sid)
        return part.stat().st_size if part.exists() else 0


async def _lock(fd: int) -> None:
    """Take the session's write lock without blocking the event loop (a retried request waits
    for the original to finish)."""
    # LOOP(devstore session lock): cap=1200 tries of 50 ms (one minute); exit=lock taken
    for _ in range(1200):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            await asyncio.sleep(0.05)
    raise TimeoutError("the upload session is busy")


def _cors(response: Response, request: Request) -> Response:
    allowed = os.environ.get("DEVSTORE_ALLOW_ORIGIN", "")
    origin = request.headers.get("origin")
    if origin and origin in {o.strip() for o in allowed.split(",") if o.strip()}:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "PUT, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Range, Content-Type"
        response.headers["Access-Control-Expose-Headers"] = "Range"
        response.headers["Access-Control-Max-Age"] = "600"
        response.headers["Vary"] = "Origin"
    return response


def _incomplete(store: Store, sid: str, request: Request) -> Response:
    done = store.committed(sid)
    response = Response(status_code=308)
    if done:
        response.headers["Range"] = f"bytes=0-{done - 1}"
    return _cors(response, request)


def _error(request: Request, message: str, status: int) -> Response:
    return _cors(JSONResponse({"error": message}, status_code=status), request)


async def upload(request: Request) -> Response:
    """``PUT /upload/{sid}``: a chunk, or a status query (``bytes */total``)."""
    store: Store = request.app.state.store
    sid = request.path_params["sid"]
    if request.method == "OPTIONS":
        return _cors(Response(status_code=204), request)
    session = store.session(sid)
    if session is None:
        return _error(request, "no such upload", 404)
    target = store.target(session["key"])
    if target.exists() and not store.partial(sid).exists():
        return _cors(JSONResponse({"size": session["size"]}, status_code=200), request)
    header = request.headers.get("content-range", "")
    if QUERY.match(header):
        return _incomplete(store, sid, request)
    match = RANGE.match(header)
    if not match:
        return _error(request, "Content-Range required", 400)
    start, end, total = (int(g) for g in match.groups())
    if total != session["size"] or end < start or end >= total:
        return _error(request, "range outside the upload", 400)
    with store.partial(sid).open("ab") as out:
        # one writer per session: a retried request that overlaps the original waits here, then
        # sees what the original committed
        await _lock(out.fileno())
        done = store.committed(sid)
        if start > done:
            return _error(request, "chunk out of order", 400)
        if end < done:  # a repeated chunk: already committed, nothing changes
            return _incomplete(store, sid, request)
        skip = done - start  # the part of this chunk already committed
        written = 0
        # LOOP(devstore chunk write): cap=the chunk's bytes as the server delivers them;
        # exit=end of body
        async for piece in request.stream():
            if skip:
                drop = min(skip, len(piece))
                piece, skip = piece[drop:], skip - drop
            if not piece:
                continue
            if done + written + len(piece) > end + 1:
                out.truncate(done + written)
                return _error(request, "body longer than its range", 400)
            out.write(piece)
            written += len(piece)
        out.flush()
        os.fsync(out.fileno())
    if store.committed(sid) < total:
        return _incomplete(store, sid, request)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(store.partial(sid), target)
    return _cors(JSONResponse({"size": total}, status_code=200), request)


def ttl_s() -> int:
    """How long an upload session lives (two days by default, like a GCS session)."""
    return int(os.environ.get("DEVSTORE_SESSION_TTL_S", "172800"))


def build(root: str | os.PathLike[str], public_url: str) -> Starlette:
    """The ASGI app (tests mount it in process)."""
    app = Starlette(routes=[Route("/upload/{sid}", upload, methods=["PUT", "OPTIONS"])])
    app.state.store = Store(root, public_url, ttl_s())
    return app


def _app() -> Starlette:
    if os.environ.get("APP_ENV") not in ("dev", "test"):
        raise RuntimeError("the development object store runs only in dev and test")
    return build(os.environ["DEVSTORE_ROOT"], os.environ["DEVSTORE_PUBLIC_URL"])


def __getattr__(name: str) -> Any:  # ``uvicorn devstore.server:app`` builds it on first use
    if name == "app":
        return _app()
    raise AttributeError(name)
