"""Kill tests for the mutation run of ``exports.contenturls`` and ``exports.ooxml`` (2026-10-30).

``clean_urls`` is called on small hand-built snapshots (every address kind once, good and bad);
the OOXML helpers on a hand-built workbook package. Hermetic: no builder, no renderer, no
network; addresses end in ``.example``.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from typing import Any

from exports import ooxml as OX
from exports.content import (
    DetectionRow,
    EvidenceRow,
    FindingRow,
    FoundVia,
    PersonRow,
    PostingRow,
    ReleaseRow,
    SnapshotContent,
    SourceRow,
)
from exports.contenturls import clean_urls

DAY = date(2026, 10, 30)


def _content(**kw: Any) -> SnapshotContent:
    base: dict[str, Any] = {
        "tenant_id": "t",
        "account_id": "a",
        "run_id": "r",
        "account_name": "Acme",
        "as_of": DAY,
        "is_salesforce_partner": False,
    }
    return SnapshotContent(**{**base, **kw})


def _source(code: str, url: str, archive_url: str = "") -> SourceRow:
    return SourceRow(code=code, url=url, published=None, kind="web", archive_url=archive_url)


def _evidence(i: str, url: str | None, source_code: str | None = None) -> EvidenceRow:
    return EvidenceRow(
        id=i,
        fact_id="f",
        evidence_kind="web",
        url=url,
        supporting_sentence="s",
        source_code=source_code,
        is_aggregator=False,
        origin=None,
        evidence_date=None,
    )


def _person(
    i: str, profile: str, second: str | None = None, via: tuple[FoundVia, ...] = ()
) -> PersonRow:
    return PersonRow(
        id=i,
        name="N",
        title="T",
        level="l",
        lanes="x",
        priority="A",
        profile_url=profile,
        source_code=None,
        second_profile_url=second,
        found_via=via,
    )


def _posting(key: str, url: str | None, archive_url: str = "") -> PostingRow:
    return PostingRow(
        posting_key=key,
        title="T",
        url=url,
        status="open",
        posting_class=None,
        archive_url=archive_url,
    )


def _detection(i: str, url: str | None) -> DetectionRow:
    return DetectionRow(
        id=i, technology="Tech", first_seen=None, last_seen=None, source_url=url, source_code=None
    )


# ---- sources and evidence ---------------------------------------------------------------------


def test_a_source_address_is_repaired_and_its_archived_copy_cleaned() -> None:
    content = _content(
        sources=(
            _source("S1", "HTTPS://Site.example/a b", "https://arch.example/c d"),
            _source("S2", "https://site.example/ok", "ftp://arch.example/x"),
        )
    )
    cleaned, dropped, bad = clean_urls(content)
    assert [(s.code, s.url, s.archive_url) for s in cleaned.sources] == [
        ("S1", "https://site.example/a%20b", "https://arch.example/c%20d"),
        ("S2", "https://site.example/ok", ""),
    ]
    assert dropped == () and bad == frozenset()
    assert isinstance(cleaned.sources, tuple) and isinstance(dropped, tuple)


def test_a_source_with_credentials_keeps_its_registry_form() -> None:
    cleaned, dropped, _ = clean_urls(
        _content(sources=(_source("S1", "https://u:p@Site.example/x"),))
    )
    assert [s.url for s in cleaned.sources] == ["https://site.example/x"]
    assert dropped == ()


def test_a_source_that_cannot_be_used_is_left_out_listed_and_cut_from_its_evidence() -> None:
    content = _content(
        sources=(_source("S1", "javascript:alert(1)"), _source("S2", "https://site.example/ok")),
        evidence=(
            _evidence("e1", "https://site.example/e 1", "S1"),
            _evidence("e2", None, "S2"),
            _evidence("e3", "javascript:x", "S2"),
            _evidence("e4", "https://site.example/e4", None),
        ),
    )
    cleaned, dropped, bad = clean_urls(content)
    assert [s.code for s in cleaned.sources] == ["S2"]
    assert dropped == (("sources", "S1", "bad_url"),)
    assert bad == frozenset()
    got = [(e.id, e.url, e.source_code) for e in cleaned.evidence]
    assert got == [
        ("e1", "https://site.example/e%201", None),  # its registered source was lost
        ("e2", None, "S2"),
        ("e3", None, "S2"),  # an unusable address is removed, the row stays
        ("e4", "https://site.example/e4", None),
    ]


def test_an_unchanged_evidence_row_is_the_same_object() -> None:
    row = _evidence("e1", None, "S9")
    cleaned, _, _ = clean_urls(_content(evidence=(row,)))
    assert cleaned.evidence[0] is row


def test_the_sources_after_a_lost_one_are_still_cleaned() -> None:
    content = _content(
        sources=(
            _source("S1", "ftp://x.example/a"),
            _source("S2", "https://b.example/b b"),
            _source("S3", "ftp://x.example/c"),
            _source("S4", "https://d.example/d d"),
        )
    )
    cleaned, dropped, _ = clean_urls(content)
    assert [s.url for s in cleaned.sources] == [
        "https://b.example/b%20b",
        "https://d.example/d%20d",
    ]
    assert [d[1] for d in dropped] == ["S1", "S3"]


# ---- people, findings -------------------------------------------------------------------------


def test_a_person_keeps_clean_links_and_a_bad_profile_link_is_listed() -> None:
    via = (
        FoundVia(origin="title_search", mention_url="https://m.example/a b"),
        FoundVia(origin="association", mention_url=None),
        FoundVia(origin="association", mention_url="javascript:x"),
        FoundVia(origin="association", mention_url=""),
    )
    content = _content(
        people=(
            _person(
                "p1", "https://www.linkedin.com/in/a b", "https://www.linkedin.com/in/c d", via
            ),
            _person("p2", "javascript:x", ""),
            _person("p3", "https://www.linkedin.com/in/ok", "ftp://x.example/y"),
        )
    )
    cleaned, dropped, bad = clean_urls(content)
    p1, p2, p3 = cleaned.people
    assert p1.profile_url == "https://www.linkedin.com/in/a%20b"
    assert p1.second_profile_url == "https://www.linkedin.com/in/c%20d"
    assert [v.mention_url for v in p1.found_via] == ["https://m.example/a%20b", None, None, ""]
    assert [v.origin for v in p1.found_via] == ["title_search"] + ["association"] * 3
    assert (p2.profile_url, p2.second_profile_url) == ("", None)
    assert p3.second_profile_url is None  # an unusable second link is removed
    assert bad == frozenset({"p2"}) and dropped == ()


def test_a_finding_without_a_usable_address_is_left_out_and_listed() -> None:
    def finding(person: str, url: str) -> FindingRow:
        return FindingRow(person_id=person, claim="c", sentence="s", url=url)

    content = _content(
        findings=(
            finding("p1", "https://l.example/a b"),
            finding("p2", "javascript:x"),
            finding("p3", "https://l.example/c"),
        )
    )
    cleaned, dropped, _ = clean_urls(content)
    assert [(f.person_id, f.url) for f in cleaned.findings] == [
        ("p1", "https://l.example/a%20b"),
        ("p3", "https://l.example/c"),
    ]
    assert dropped == (("summary.linkedin_findings", "p2", "bad_url"),)


# ---- postings, detections, releases -----------------------------------------------------------


def test_a_posting_address_is_repaired_or_the_row_is_marked_bad() -> None:
    content = _content(
        postings=(
            _posting("k1", "https://j.example/a b", "https://arch.example/x y"),
            _posting("k2", None),
            _posting("k3", ""),
            _posting("k4", "javascript:x", "ftp://arch.example/z"),
        )
    )
    cleaned, _, bad = clean_urls(content)
    assert [(p.posting_key, p.url, p.archive_url) for p in cleaned.postings] == [
        ("k1", "https://j.example/a%20b", "https://arch.example/x%20y"),
        ("k2", None, ""),
        ("k3", None, ""),
        ("k4", None, ""),
    ]
    assert bad == frozenset({"k4"})  # a missing address is not a bad one


def test_a_detection_address_is_repaired_or_the_row_is_marked_bad() -> None:
    content = _content(
        detections=(
            _detection("d1", "https://t.example/a b"),
            _detection("d2", None),
            _detection("d3", ""),
            _detection("d4", "javascript:x"),
        )
    )
    cleaned, _, bad = clean_urls(content)
    assert [(d.id, d.source_url) for d in cleaned.detections] == [
        ("d1", "https://t.example/a%20b"),
        ("d2", None),
        ("d3", None),
        ("d4", None),
    ]
    assert bad == frozenset({"d4"})


def test_a_release_with_a_bad_address_is_left_out_and_listed_by_a_digest() -> None:
    def release(url: str) -> ReleaseRow:
        return ReleaseRow(kind="news", url=url, title="T", day=DAY, text="x")

    content = _content(
        releases=(
            release("https://r.example/a b"),
            release("javascript:x"),
            release("https://r.example/c"),
        )
    )
    cleaned, dropped, _ = clean_urls(content)
    assert [r.url for r in cleaned.releases] == ["https://r.example/a%20b", "https://r.example/c"]
    digest = hashlib.sha256(b"javascript:x").hexdigest()[:8]
    assert dropped == (("workspace.releases", digest, "bad_url"),)
    assert len(digest) == 8


def test_nothing_to_clean_returns_the_content_unchanged_and_the_empty_results() -> None:
    content = _content()
    cleaned, dropped, bad = clean_urls(content)
    assert cleaned == content and dropped == () and bad == frozenset()


def test_every_kind_is_returned_together_with_the_drops_in_order() -> None:
    content = _content(
        sources=(_source("S1", "ftp://x.example"),),
        findings=(FindingRow(person_id="p9", claim="c", sentence="s", url="ftp://x.example"),),
        releases=(ReleaseRow(kind="news", url="ftp://x.example", title="T", day=DAY, text="x"),),
    )
    _, dropped, _ = clean_urls(content)
    assert [d[0] for d in dropped] == ["sources", "summary.linkedin_findings", "workspace.releases"]


# ---- exports.ooxml ----------------------------------------------------------------------------


ENTRY = (2026, 10, 30, 0, 0, 0)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

CONTENT_TYPES = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="wb"/>'
    '<Override PartName="/docProps/thumbnail.jpeg" ContentType="image/jpeg"/>'
    '<Override PartName="/docProps/custom.xml" ContentType="custom"/>'
    '<Override PartName="/customXml/item1.xml" ContentType="item"/>'
    "</Types>"
)
ROOT_RELS = (
    f'<Relationships xmlns="{PKG_REL}">'
    '<Relationship Id="rId1" Type="t" Target="xl/workbook.xml"/>'
    '<Relationship Id="rId2" Type="t" Target="docProps/thumbnail.jpeg"/>'
    '<Relationship Id="rId3" Type="t" Target="/docProps/custom.xml"/>'
    '<Relationship Id="rId4" Type="t" Target="../customXml/item1.xml"/>'
    '<Relationship Id="rId5" Type="t"/>'
    "</Relationships>"
)
WORKBOOK = (
    f'<workbook xmlns="{MAIN}" xmlns:r="{REL_NS}"><sheets>'
    '<sheet name="Counts" sheetId="1" r:id="rId1"/>'
    '<sheet name="Other" sheetId="2" r:id="rId2"/>'
    '<sheet name="Ghost" sheetId="3" r:id="rId9"/>'
    '<sheet name="" sheetId="4" r:id="rId1"/>'
    '<sheet name="NoRel" sheetId="5"/>'
    '<sheet name="Xtra" sheetId="6" r:id="rId3"/>'
    "</sheets>"
    '<externalReferences><externalReference name="NotASheet" r:id="rId1"/></externalReferences>'
    "</workbook>"
)
WORKBOOK_RELS = (
    f'<Relationships xmlns="{PKG_REL}">'
    '<Relationship Id="rId1" Type="t" Target="worksheets/sheet1.xml"/>'
    '<Relationship Id="rId2" Type="t" Target="/xl/worksheets/sheet2.xml"/>'
    '<Relationship Id="rId3" Type="t" Target="/Xtra/sheet.xml"/>'
    "</Relationships>"
)
SHEET1 = (
    f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
    '<c r="A1"><v>1</v></c>'
    '<c r="B7" s="3" t="n"><f>SUM(A1)</f><v></v></c>'
    '<c r="C7"><f>A1&amp;"x"</f><v/></c>'
    '<c r="D7" s="1"><f>NOW()</f><v></v></c>'
    '<c r="E7" t="str"><f>A1</f><v>stale</v></c>'
    "</row></sheetData></worksheet>"
)
SHEET2 = (
    f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
    '<c r="A1" t="str"><f>B1</f><v>old</v></c><c r="B1"><f>A1</f><v></v></c>'
    "</row></sheetData></worksheet>"
)
SHEET3 = (
    f'<worksheet xmlns="{MAIN}"><sheetData><row r="1">'
    '<c r="A1"><f>1</f><v></v></c></row></sheetData></worksheet>'
)


def _package(extra: dict[str, bytes] | None = None, *, dirs: tuple[str, ...] = ()) -> bytes:
    members: dict[str, bytes] = {
        "xl/worksheets/sheet2.xml": SHEET2.encode(),
        "xl/worksheets/sheet1.xml": SHEET1.encode(),
        "xl/workbook.xml": WORKBOOK.encode(),
        "xl/_rels/workbook.xml.rels": WORKBOOK_RELS.encode(),
        "_rels/.rels": ROOT_RELS.encode(),
        "[Content_Types].xml": CONTENT_TYPES.encode(),
        "docProps/thumbnail.jpeg": b"jpeg",
        "docProps/custom.xml": b"<custom/>",
        "customXml/item1.xml": b"<item/>",
        "docProps/core.xml": b"<old core/>",
        "Xtra/sheet.xml": SHEET3.encode(),
        **(extra or {}),
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
        for d in dirs:
            z.writestr(zipfile.ZipInfo(d), b"")
    return out.getvalue()


def _open(data: bytes) -> tuple[list[str], dict[str, bytes], zipfile.ZipFile]:
    z = zipfile.ZipFile(io.BytesIO(data))
    return z.namelist(), {n: z.read(n) for n in z.namelist()}, z


def _normalise(data: bytes, **kw: Any) -> bytes:
    args: dict[str, Any] = {
        "title": "T & <Co>",
        "author": "Author",
        "company": "Co & Sons",
        "as_of": DAY,
        "entry_time": ENTRY,
    }
    return OX.normalise_package(data, **{**args, **kw})


CORE_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
    'core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<dc:title>Q &amp; A &lt;1&gt;</dc:title><dc:creator>Au&amp;thor</dc:creator>"
    "<cp:lastModifiedBy>Au&amp;thor</cp:lastModifiedBy>"
    '<dcterms:created xsi:type="dcterms:W3CDTF">2026-03-04T00:00:00Z</dcterms:created>'
    '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-03-04T00:00:00Z</dcterms:modified>'
    "</cp:coreProperties>"
)
APP_DOCUMENT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    "<Company>A &amp; B</Company></Properties>"
)


def test_the_core_and_app_documents_are_exactly_these() -> None:
    assert OX.core_xml("Q & A <1>", "Au&thor", date(2026, 3, 4)) == CORE_DOCUMENT.encode()
    assert OX.app_xml("A & B") == APP_DOCUMENT.encode()


def test_the_core_and_app_documents_are_fixed() -> None:
    core = OX.core_xml("Q & A <1>", "Au&thor", date(2026, 3, 4)).decode()
    assert core.startswith(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties '
    )
    assert "<dc:title>Q &amp; A &lt;1&gt;</dc:title>" in core
    assert "<dc:creator>Au&amp;thor</dc:creator>" in core
    assert "<cp:lastModifiedBy>Au&amp;thor</cp:lastModifiedBy>" in core
    assert (
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-03-04T00:00:00Z</dcterms:created>' in core
    )
    assert (
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-03-04T00:00:00Z</dcterms:modified>'
        in core
    )
    assert core.endswith("</cp:coreProperties>")
    app = OX.app_xml("A & B").decode()
    assert app.startswith('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties ')
    assert app.endswith("<Company>A &amp; B</Company></Properties>")
    assert "Application" not in app


def test_a_normalised_package_has_a_fixed_order_time_and_attributes() -> None:
    data = _normalise(_package(dirs=("empty/",)))
    names, _, z = _open(data)
    assert names[0] == "[Content_Types].xml"
    assert names[1:] == sorted(names[1:])
    assert "empty/" not in names
    for info in z.infolist():
        assert info.date_time == ENTRY
        assert info.compress_type == zipfile.ZIP_DEFLATED
        assert info.create_system == 0
        assert info.external_attr == 0o600 << 16
    assert _normalise(_package()) == _normalise(_package())


def test_a_stored_package_is_not_compressed() -> None:
    _, _, z = _open(_normalise(_package(), compression="store"))
    assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_STORED}
    _, _, z2 = _open(_normalise(_package(), compression="deflate"))
    assert {i.compress_type for i in z2.infolist()} == {zipfile.ZIP_DEFLATED}


def test_the_properties_are_replaced_and_the_thumbnail_and_custom_parts_dropped() -> None:
    names, members, _ = _open(_normalise(_package()))
    assert "docProps/thumbnail.jpeg" not in names
    assert "docProps/custom.xml" not in names
    assert "customXml/item1.xml" not in names
    assert members["docProps/core.xml"] == OX.core_xml("T & <Co>", "Author", DAY)
    assert members["docProps/app.xml"] == OX.app_xml("Co & Sons")
    assert members["xl/workbook.xml"] == WORKBOOK.encode()  # every other part byte for byte


def test_relationships_and_overrides_that_point_at_dropped_parts_are_removed() -> None:
    _, members, _ = _open(_normalise(_package()))
    types = members["[Content_Types].xml"].decode()
    assert "thumbnail" not in types and "custom.xml" not in types and "item1" not in types
    assert '<Override PartName="/xl/workbook.xml" ContentType="wb"/>' in types
    assert '<Default Extension="xml" ContentType="application/xml"/>' in types
    rels = members["_rels/.rels"].decode()
    assert rels == (
        f'<Relationships xmlns="{PKG_REL}">'
        '<Relationship Id="rId1" Type="t" Target="xl/workbook.xml"/>'
        '<Relationship Id="rId5" Type="t"/>'
        "</Relationships>"
    )  # all three spellings of a dropped target go; a relationship without a target stays


def test_nothing_is_rewritten_when_nothing_is_dropped() -> None:
    plain = _package()
    # the same package without the dropped parts keeps its relationship parts untouched
    clean = {
        "_rels/.rels": b'<Relationships><Relationship Id="rId1" Target="x"/></Relationships>',
        "[Content_Types].xml": b"<Types/>",
        "xl/workbook.xml": b"<w/>",
    }
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, b in clean.items():
            z.writestr(n, b)
    _, members, _ = _open(_normalise(out.getvalue()))
    assert members["_rels/.rels"] == clean["_rels/.rels"]
    assert members["[Content_Types].xml"] == b"<Types/>"
    assert plain  # (the full package above is the dropping case)


def test_the_target_of_a_relationship_loses_its_leading_dots_and_slashes() -> None:
    assert OX._target('<Relationship Target="../customXml/item1.xml"/>') == "customXml/item1.xml"
    assert OX._target('<Relationship Target="/docProps/custom.xml"/>') == "docProps/custom.xml"
    assert OX._target('<Relationship Target="../../a/b"/>') == "a/b"
    assert OX._target('<Relationship Target="a/b"/>') == "a/b"
    assert OX._target('<Relationship Id="x"/>') == ""
    assert OX._dropped("docProps/thumbnail.jpeg") and OX._dropped("customXml/x.xml")
    assert OX._dropped("docProps/custom.xml") and not OX._dropped("docProps/core.xml")


def test_overrides_are_matched_on_the_whole_part_name() -> None:
    xml = (
        b'<Types><Override PartName="/docProps/custom.xml" a="b"/>'
        b'<Override PartName="/docProps/customXxml" a="b"/>'
        b'<Override ContentType="c" PartName="/docProps/custom.xml"/></Types>'
    )
    out = OX._strip_refs(xml, ["docProps/custom.xml"])
    assert out == b'<Types><Override PartName="/docProps/customXxml" a="b"/></Types>'


# ---- cached formula values --------------------------------------------------------------------


def _cached(values: dict[tuple[str, str], Any], **kw: Any) -> tuple[bytes, int, tuple[str, ...]]:
    return OX.cache_formula_values(_package(), values, entry_time=ENTRY, **kw)


def test_formula_cells_receive_their_values_with_the_type_attribute() -> None:
    data, cached, missing = _cached(
        {
            ("Counts", "B7"): 42,
            ("Counts", "C7"): 'a&b<"x"',
            ("Counts", "D7"): True,
            ("Counts", "E7"): 0.5,
        }
    )
    _, members, _ = _open(data)
    sheet = members["xl/worksheets/sheet1.xml"].decode()
    assert '<c r="B7" s="3"><f>SUM(A1)</f><v>42</v></c>' in sheet  # the old type attribute is gone
    assert '<c r="C7" t="str"><f>A1&amp;"x"</f><v>a&amp;b&lt;"x"</v></c>' in sheet
    assert '<c r="D7" s="1" t="b"><f>NOW()</f><v>1</v></c>' in sheet
    assert '<c r="E7" t="str"><f>A1</f><v>stale</v></c>' in sheet  # a cached cell is not matched
    assert '<c r="A1"><v>1</v></c>' in sheet
    assert (cached, missing) == (3, ("Counts!E7",))


def test_a_formula_cell_the_plan_does_not_name_is_left_exactly_as_written() -> None:
    data, cached, missing = _cached({("Counts", "B7"): 1})
    sheet = _open(data)[1]["xl/worksheets/sheet1.xml"].decode()
    assert sheet == SHEET1.replace(
        '<c r="B7" s="3" t="n"><f>SUM(A1)</f><v></v></c>',
        '<c r="B7" s="3"><f>SUM(A1)</f><v>1</v></c>',
    )
    assert (cached, missing) == (1, ())


def test_the_cached_count_adds_up_over_the_sheets() -> None:
    _, cached, missing = _cached({("Counts", "B7"): 1, ("Counts", "C7"): 2, ("Other", "B1"): 3})
    assert (cached, missing) == (3, ())


def test_every_value_type_and_a_false_boolean() -> None:
    assert OX._value_xml(True) == (' t="b"', "1")
    assert OX._value_xml(False) == (' t="b"', "0")
    assert OX._value_xml(7) == ("", "7")
    assert OX._value_xml(-3) == ("", "-3")
    assert OX._value_xml(0.1) == ("", "0.1")
    assert OX._value_xml(1e-07) == ("", "1e-07")
    assert OX._value_xml("x<y") == (' t="str"', "x&lt;y")
    assert OX._value_xml("") == (' t="str"', "")


def test_a_zero_value_is_still_a_value() -> None:
    data, cached, missing = _cached({("Counts", "B7"): 0, ("Counts", "C7"): ""})
    sheet = _open(data)[1]["xl/worksheets/sheet1.xml"].decode()
    assert '<c r="B7" s="3"><f>SUM(A1)</f><v>0</v></c>' in sheet
    assert '<c r="C7" t="str"><f>A1&amp;"x"</f><v></v></c>' in sheet
    assert (cached, missing) == (2, ())


def test_cells_the_plan_does_not_know_and_unknown_sheets_are_reported_in_order() -> None:
    data, cached, missing = _cached(
        {
            ("Counts", "B7"): 1,
            ("Counts", "Z9"): 2,
            ("Counts", "A9"): 3,
            ("Nowhere", "B2"): 4,
            ("Nowhere", "A1"): 5,
            ("Ghost", "A1"): 6,
        }
    )
    assert cached == 1
    assert missing == ("Counts!A9", "Counts!Z9", "Nowhere!A1", "Nowhere!B2", "Ghost!A1")
    sheet2 = _open(data)[1]["xl/worksheets/sheet2.xml"].decode()
    assert sheet2 == SHEET2  # a sheet with no wanted cell is left alone


def test_a_sheet_whose_part_is_absent_is_missing_wholly() -> None:
    package = _package()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            if info.filename != "xl/worksheets/sheet1.xml":
                dst.writestr(info.filename, src.read(info))
    _, cached, missing = OX.cache_formula_values(
        out.getvalue(), {("Counts", "B7"): 1, ("Other", "B1"): 2}, entry_time=ENTRY
    )
    assert cached == 1 and missing == ("Counts!B7",)


def test_the_absolute_target_of_a_sheet_part_is_read() -> None:
    data, cached, missing = _cached({("Other", "A1"): "new", ("Other", "B1"): "new"})
    sheet2 = _open(data)[1]["xl/worksheets/sheet2.xml"].decode()
    assert cached == 1 and missing == ("Other!A1",)  # A1 is already cached: not matched
    assert '<c r="B1" t="str"><f>A1</f><v>new</v></c>' in sheet2
    assert OX._sheet_parts(_open(_package())[1]) == {
        "Counts": "xl/worksheets/sheet1.xml",
        "Other": "xl/worksheets/sheet2.xml",
        "Xtra": "Xtra/sheet.xml",  # an absolute target loses only its leading slash
    }
    data2, cached2, missing2 = _cached({("Xtra", "A1"): 5})
    assert cached2 == 1 and missing2 == ()
    assert b"<v>5</v>" in _open(data2)[1]["Xtra/sheet.xml"]


def test_a_cached_package_is_deterministic_and_uses_the_entry_time_and_method() -> None:
    values = {("Counts", "B7"): 1}
    a, _, _ = _cached(values)
    b, _, _ = _cached(values)
    assert a == b
    names, _, z = _open(a)
    assert names[0] == "[Content_Types].xml" and names[1:] == sorted(names[1:])
    assert {i.date_time for i in z.infolist()} == {ENTRY}
    stored, _, zs = _open(_cached(values, compression="store")[0])
    assert {i.compress_type for i in zs.infolist()} == {zipfile.ZIP_STORED}
    assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_DEFLATED}
    assert stored


def test_other_parts_are_kept_byte_for_byte_when_values_are_cached() -> None:
    _, members, _ = _open(_cached({("Counts", "B7"): 1})[0])
    assert members["xl/workbook.xml"] == WORKBOOK.encode()
    assert members["docProps/core.xml"] == b"<old core/>"
    assert members["xl/worksheets/sheet2.xml"] == SHEET2.encode()


# ---- strip_cached_values ----------------------------------------------------------------------


def test_stored_results_are_removed_from_formula_cells_only() -> None:
    cached, _, _ = _cached({("Counts", "B7"): 42, ("Counts", "C7"): "t"})
    stripped = OX.strip_cached_values(cached)
    sheet = _open(stripped)[1]["xl/worksheets/sheet1.xml"].decode()
    assert '<c r="B7" s="3"><f>SUM(A1)</f><v></v></c>' in sheet
    assert '<c r="C7"><f>A1&amp;"x"</f><v></v></c>' in sheet
    assert '<c r="E7"><f>A1</f><v></v></c>' in sheet  # a stale result and its type attribute go
    assert '<c r="A1"><v>1</v></c>' in sheet  # a plain value stays
    assert _open(stripped)[1]["xl/worksheets/sheet2.xml"].decode() == (
        SHEET2.replace(' t="str"', "").replace("<v>old</v>", "<v></v>")
    )


def test_a_package_with_no_formula_part_is_returned_as_it_is() -> None:
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f'<worksheet xmlns="{MAIN}"><c r="A1"><v>1</v></c></worksheet>',
        )
        z.writestr("other/sheet.xml", "<c r='A1'><f>1</f><v>1</v></c>")
    data = plain.getvalue()
    assert OX.strip_cached_values(data) is data
    no_dir = io.BytesIO()
    with zipfile.ZipFile(no_dir, "w") as z:
        z.writestr("other/sheet.xml", '<c r="A1"><f>1</f><v>1</v></c>')
    assert OX.strip_cached_values(no_dir.getvalue()) == no_dir.getvalue()


def test_stripping_keeps_every_entry_attribute_and_compression() -> None:
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as z:
        info = zipfile.ZipInfo("xl/worksheets/sheet1.xml", date_time=(2020, 1, 2, 3, 4, 6))
        info.external_attr = 0o640 << 16
        z.writestr(info, SHEET1.replace("<v></v>", "<v>9</v>"), compress_type=zipfile.ZIP_STORED)
        other = zipfile.ZipInfo("docProps/x.xml", date_time=(2021, 2, 3, 4, 5, 6))
        z.writestr(other, b"x", compress_type=zipfile.ZIP_DEFLATED)
    stripped = OX.strip_cached_values(package.getvalue())
    with zipfile.ZipFile(io.BytesIO(stripped)) as z:
        a, b = z.infolist()
        assert (a.filename, a.date_time, a.compress_type, a.external_attr) == (
            "xl/worksheets/sheet1.xml",
            (2020, 1, 2, 3, 4, 6),
            zipfile.ZIP_STORED,
            0o640 << 16,
        )
        assert (b.filename, b.compress_type, z.read(b)) == (
            "docProps/x.xml",
            zipfile.ZIP_DEFLATED,
            b"x",
        )
        assert "<v>9</v>" not in z.read(a).decode()


def test_a_member_is_compressed_at_level_six() -> None:
    import random
    import struct
    import zlib

    rng = random.Random(1)
    words = [
        "".join(rng.choice("abcdefghij") for _ in range(rng.randint(2, 7))) for _ in range(300)
    ]
    body = " ".join(rng.choice(words) for _ in range(40_000)).encode()
    data = OX._pack({"[Content_Types].xml": body}, ENTRY, zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.infolist()[0]
    name_len, extra_len = struct.unpack(
        "<HH", data[info.header_offset + 26 : info.header_offset + 30]
    )
    start = info.header_offset + 30 + name_len + extra_len
    stored = data[start : start + info.compress_size]
    expected = zlib.compressobj(6, zlib.DEFLATED, -15)
    assert stored == expected.compress(body) + expected.flush()
