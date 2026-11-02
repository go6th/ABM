#!/usr/bin/env python3
"""Build ``config/places.json``, the city list the Ask "nearby" expander reads
(``documents.places``), from the GeoNames ``cities15000`` extract as packaged by the MIT-licensed
``geonamescache`` project. Run once when the list is to be refreshed:

    python scripts/build_places.py <path to geonamescache/data>

Every US city of at least 15,000 people and every other city of at least 100,000 is kept, as
``[name, ascii name, country, admin1, latitude, longitude, population]``. GeoNames data is
licensed CC BY 4.0 (https://www.geonames.org/); the attribution is written into the file.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "config" / "places.json"
US_MIN, WORLD_MIN = 15_000, 100_000


LETTERS = str.maketrans(
    {
        "ł": "l",
        "Ł": "L",
        "ø": "o",
        "Ø": "O",
        "đ": "d",
        "Đ": "D",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
    }
)


def ascii_fold(text: str) -> str:
    """The name in plain letters; ł, ø, đ and ß have no accent to strip and are mapped."""
    plain = unicodedata.normalize("NFKD", text.translate(LETTERS))
    return plain.encode("ascii", "ignore").decode().strip()


def main(data: Path) -> None:
    cities = json.loads((data / "cities15000.json").read_text())
    countries = json.loads((data / "countries.json").read_text())
    states = json.loads((data / "us_states.json").read_text())
    places = []
    for c in cities.values():
        pop, cc = int(c["population"]), c["countrycode"]
        if pop < (US_MIN if cc == "US" else WORLD_MIN):
            continue
        places.append(
            [
                c["name"],
                ascii_fold(c["name"]),
                cc,
                str(c["admin1code"]),
                round(float(c["latitude"]), 4),
                round(float(c["longitude"]), 4),
                pop,
            ]
        )
    places.sort(key=lambda p: (-p[6], p[0]))
    doc = {
        "source": "GeoNames cities15000 (CC BY 4.0, https://www.geonames.org/) via geonamescache (MIT)",
        "fields": ["name", "ascii", "country", "admin1", "lat", "lon", "population"],
        "countries": {v["iso"]: v["name"] for v in countries.values()},
        "us_states": {code: s["name"] for code, s in states.items()},
        "places": places,
    }
    OUT.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"wrote {OUT} ({len(places)} places, {OUT.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
