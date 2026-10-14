"""Images and audio or video: metadata by code, text through engines the platform may not have.

* Images: format, size, mode and selected EXIF fields (Pillow); a GPS position in the EXIF is
  personal data (flagged, never shown). OCR needs an engine (``pytesseract`` and the tesseract
  binary); without one the image is stored with status PARTIAL ``ocr_unavailable``.
* Audio and video: container, size and, for WAV, the duration (stdlib ``wave``). A transcript
  needs a speech model behind the model router; none is configured (the platform's model provider
  takes text and images, not audio), so the file is stored with status PARTIAL
  ``transcription_unavailable``. The cost gate still applies the moment one is configured.

GUARANTEE(test_every_supported_type_is_detected_and_parsed)
"""

from __future__ import annotations

import io
import wave
from collections.abc import Iterator
from typing import IO, Any

from research_files.checks import Finding
from research_files.parsers.base import ParseContext, Unit, fatal

_EXIF = {271: "camera_make", 272: "camera_model", 306: "taken_at", 305: "software"}


def ocr_available() -> bool:
    """Whether an OCR engine is installed."""
    try:
        import shutil

        import pytesseract  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


class ImageParser:
    """See the module docstring."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """One unit: the image's metadata (and its OCR text when an engine exists)."""
        if cursor.get("done"):
            return
        from PIL import Image, UnidentifiedImageError

        stream.seek(0)
        try:
            with Image.open(stream) as probe:
                probe.verify()
            stream.seek(0)
            image = Image.open(stream)
            image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
            yield fatal(
                "note",
                {},
                "corrupt_file",
                f"The image cannot be decoded ({type(error).__name__}); it may be truncated.",
                "Export the image again and upload it.",
            )
            return
        meta: dict[str, Any] = {
            "format": image.format,
            "width": image.width,
            "height": image.height,
            "mode": image.mode,
        }
        findings: list[Finding] = []
        exif = image.getexif() if hasattr(image, "getexif") else {}
        for tag, name in _EXIF.items():
            if tag in exif:
                meta[name] = str(exif[tag])[:80]
        if exif and 0x8825 in exif:
            findings.append(
                Finding(
                    "warn",
                    "personal_data_found",
                    {"kind": "gps_location"},
                    "The image carries a GPS position in its metadata. Its value is not shown.",
                    "Strip the location metadata before sharing the image.",
                )
            )
        text = ""
        if ocr_available():  # pragma: no cover - no engine on this platform today
            import pytesseract

            text = pytesseract.image_to_string(image)
        else:
            findings.append(
                Finding(
                    "info",
                    "ocr_unavailable",
                    {},
                    "No OCR engine is installed, so text in the image is not read.",
                    "Ask an operator to install an OCR engine, then resume this file.",
                )
            )
        image.close()
        yield Unit(
            "image",
            {"image": ctx.name},
            text,
            {"done": True},
            findings,
            stats={
                "metadata": meta,
                "ocr": bool(text),
                "stop": None if text else "ocr_unavailable",
            },
        )


class MediaParser:
    """See the module docstring."""

    def units(self, stream: IO[bytes], cursor: dict[str, Any], ctx: ParseContext) -> Iterator[Unit]:
        """One unit: the container metadata."""
        if cursor.get("done"):
            return
        stream.seek(0, io.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        meta: dict[str, Any] = {"bytes": size}
        findings: list[Finding] = []
        head = stream.read(16)
        stream.seek(0)
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            try:
                with wave.open(stream) as audio:
                    meta.update(
                        channels=audio.getnchannels(),
                        sample_rate=audio.getframerate(),
                        seconds=round(audio.getnframes() / max(1, audio.getframerate()), 3),
                    )
                    declared = audio.getnframes() * audio.getsampwidth() * audio.getnchannels()
                    actual = len(audio.readframes(audio.getnframes()))
                if actual < declared:
                    findings.append(
                        Finding(
                            "error",
                            "truncated_or_corrupt",
                            {},
                            f"The recording declares {declared} bytes of audio but holds"
                            f" {actual}; it is truncated.",
                            "Export the audio again and upload it.",
                        )
                    )
            except (wave.Error, EOFError) as error:
                yield fatal(
                    "note",
                    {},
                    "corrupt_file",
                    f"The WAV file cannot be read ({type(error).__name__}); it may be truncated.",
                    "Export the audio again and upload it.",
                )
                return
        findings.append(
            Finding(
                "info",
                "transcription_unavailable",
                {},
                "No speech-to-text model is configured, so this recording is stored but not"
                " transcribed.",
                "Ask an operator to configure a transcription model, then resume this file.",
            )
        )
        yield Unit(
            "media",
            {"media": ctx.name},
            "",
            {"done": True},
            findings,
            stats={"metadata": meta, "stop": "transcription_unavailable"},
        )
