"""White-label branding as validated data (VW6, D-64 proposal).

* Logo: PNG or JPEG only, sniffed from the bytes (never the file name or the declared type);
  SVG and every other format refused; size and pixel caps checked BEFORE decoding (decompression
  bombs); decoded and RE-ENCODED to a fresh PNG (metadata, trailing data and any polyglot payload
  are dropped), resized to fit; scanned by the malware scanner before it is accepted.
* Accent colour: a ``#rrggbb`` token whose contrast against white meets the configured floor
  (WCAG relative luminance; floor 4.5 is a proposal).
* Display name: control and direction characters removed, length capped, client-output scanned.
* Tokens: what views and the Word and PDF header receive: display name, accent, logo reference
  and the attribution, which stays "6th agents" whatever the branding (skill rule 6; replacing it
  needs decision D-64). Branding is never raw CSS or HTML.

GUARANTEE(test_branding_refuses_every_hostile_input)
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml
from PIL import Image, UnidentifiedImageError

from account_intelligence.config import CONFIG_DIR
from account_intelligence.safety.client_output import ClientOutputLeak, assert_client_safe
from views.render_safe import text_cell

_HEX = re.compile(r"^#[0-9a-f]{6}$")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


class BrandingRefused(ValueError):
    """A branding input that must not be stored; ``code`` is a fixed reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BrandingLimits:
    """``config/views.yaml`` branding limits (proposals)."""

    max_logo_bytes: int
    max_logo_pixels: int
    max_logo_side: int
    output_max_side: int
    min_accent_contrast: float
    max_display_name_chars: int
    attribution: str


@lru_cache
def load_limits() -> BrandingLimits:
    """The branding limits from configuration (read once)."""
    raw = yaml.safe_load((CONFIG_DIR / "views.yaml").read_text())["branding"]
    return BrandingLimits(
        max_logo_bytes=int(raw["max_logo_bytes"]),
        max_logo_pixels=int(raw["max_logo_pixels"]),
        max_logo_side=int(raw["max_logo_side"]),
        output_max_side=int(raw["output_max_side"]),
        min_accent_contrast=float(raw["min_accent_contrast"]),
        max_display_name_chars=int(raw["max_display_name_chars"]),
        attribution=str(raw["attribution"]),
    )


@dataclass(frozen=True)
class Logo:
    """A re-encoded logo ready to store."""

    png: bytes
    sha256: str
    width: int
    height: int


def sniff(data: bytes) -> str:
    """``png`` or ``jpeg`` from the leading bytes; BrandingRefused otherwise (SVG included)."""
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    raise BrandingRefused("logo_type_refused")


def process_logo(data: bytes, limits: BrandingLimits | None = None) -> Logo:
    """Validate and re-encode an uploaded logo."""
    lim = limits or load_limits()
    if not data or len(data) > lim.max_logo_bytes:
        raise BrandingRefused("logo_too_large")
    kind = sniff(data)
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format is None or probe.format.lower() not in ("png", "jpeg"):
                raise BrandingRefused("logo_type_refused")
            if probe.format.lower() != kind:
                raise BrandingRefused("logo_type_mismatch")
            w, h = probe.size
            if w < 1 or h < 1 or w * h > lim.max_logo_pixels or max(w, h) > lim.max_logo_side:
                raise BrandingRefused("logo_dimensions_refused")
            probe.load()
            image = probe.convert("RGBA")
    except BrandingRefused:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise BrandingRefused("logo_unreadable") from None
    image.thumbnail((lim.output_max_side, lim.output_max_side))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    png = out.getvalue()
    return Logo(png, hashlib.sha256(png).hexdigest(), image.width, image.height)


def _luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else float(((v + 0.055) / 1.055) ** 2.4)

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_with_white(hex_colour: str) -> float:
    """The WCAG contrast ratio of ``hex_colour`` against white."""
    return 1.05 / (_luminance(hex_colour) + 0.05)


def accent(value: str, limits: BrandingLimits | None = None) -> str:
    """A validated accent token."""
    lim = limits or load_limits()
    token = value.strip().lower()
    if not _HEX.match(token):
        raise BrandingRefused("accent_not_a_colour")
    if contrast_with_white(token) < lim.min_accent_contrast:
        raise BrandingRefused("accent_low_contrast")
    return token


def display_name(value: str, limits: BrandingLimits | None = None) -> str:
    """A validated display name."""
    lim = limits or load_limits()
    name = text_cell(value, lim.max_display_name_chars + 1)
    if not name or len(name) > lim.max_display_name_chars:
        raise BrandingRefused("display_name_length")
    if re.search(r"[<>{}\[\]\\`]", name):
        raise BrandingRefused("display_name_characters")
    try:
        assert_client_safe([name])
    except ClientOutputLeak:
        raise BrandingRefused("display_name_refused") from None
    return name


def tokens(branding: Any | None, limits: BrandingLimits | None = None) -> dict[str, str | None]:
    """What views and document headers receive (the attribution is never removable here)."""
    lim = limits or load_limits()
    if branding is None:
        return {
            "display_name": None,
            "accent": None,
            "logo_sha256": None,
            "attribution": lim.attribution,
        }
    return {
        "display_name": branding.display_name,
        "accent": branding.accent_hex,
        "logo_sha256": branding.logo_sha256,
        "attribution": lim.attribution,
    }


async def record_asset(s: Any, logo: Logo, key: str, actor: str, now: Any) -> None:
    """Record a stored logo (insert-only; the same bytes twice are one asset). The one writer of
    ``branding_assets`` (W1)."""
    from sqlalchemy import text

    await s.execute(
        text(
            "INSERT INTO branding_assets (sha256, object_key, media_type, width, height, bytes,"
            " uploaded_by, created_at) VALUES (:h, :k, 'image/png', :w, :ht, :b, :u, :now)"
            " ON CONFLICT (tenant_id, sha256) DO NOTHING"
        ),
        {
            "h": logo.sha256,
            "k": key,
            "w": logo.width,
            "ht": logo.height,
            "b": len(logo.png),
            "u": actor,
            "now": now,
        },
    )


async def asset_exists(s: Any, sha256: str) -> bool:
    """True when the context tenant stored a logo with this hash."""
    from sqlalchemy import text

    found = (
        await s.execute(text("SELECT 1 FROM branding_assets WHERE sha256 = :h"), {"h": sha256})
    ).first()
    return found is not None
