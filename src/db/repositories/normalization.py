from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.db.errors import DataLayerError


def normalize_source_url(url: str) -> str:
    """Canonicalize source URLs without discarding query parameters that may change content."""
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise DataLayerError("source URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise DataLayerError("source URL must not contain user credentials")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise DataLayerError("source URL has an invalid host or port") from exc
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))

def source_url_hash(url: str) -> str:
    return hashlib.sha256(normalize_source_url(url).encode("utf-8")).hexdigest()

def fingerprint_value(attribute_key: str, value: object) -> str:
    """Hash stable normalized JSON; object key order and string whitespace do not matter."""
    normalized_key = " ".join(attribute_key.casefold().split())

    def normalize(item: object) -> object:
        if isinstance(item, str):
            return " ".join(item.split())
        if isinstance(item, list):
            return [normalize(element) for element in item]
        if isinstance(item, dict):
            return {str(key): normalize(item[key]) for key in sorted(item)}
        return item

    try:
        payload = json.dumps(
            {"attribute_key": normalized_key, "value": normalize(value)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DataLayerError(f"fact value is not valid JSON: {exc}") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
