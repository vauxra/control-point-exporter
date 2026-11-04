from pathlib import Path

import pytest

from control_point_exporter.extractor import EAD2002_NS, EAD3_NS, TERM_TYPES, extract_file

FIXTURES = Path(__file__).parent / "fixtures"


def document(content, *, version="EAD2002", namespace=True, metadata=True):
    ns = EAD2002_NS if version == "EAD2002" else EAD3_NS
    header = "eadheader" if version == "EAD2002" else "control"
    ident = "eadid" if version == "EAD2002" else "recordid"
    attributes = f' xmlns="{ns}"' if namespace else ""
    meta = f"<{ident}>0001</{ident}><filedesc><titlestmt><titleproper>Title</titleproper></titlestmt></filedesc>" if metadata else ""
    did = "<did><unittitle>Collection title</unittitle></did>" if metadata else ""
    return f"<ead{attributes}><{header}>{meta}</{header}><archdesc>{did}{content}</archdesc></ead>"


def run_xml(tmp_path, content, *, encoding="utf-8"):
    path = tmp_path / "finding aid ü.xml"
    path.write_bytes(content.encode(encoding))
    return extract_file(path)


def problems(result):
    return " ".join(issue.problem for issue in result.issues)


def test_fixture_all_types_order_duplicates_and_source():
    result = extract_file(FIXTURES / "ead2002.xml")
    assert not result.issues
    assert result.version == "EAD2002"
    assert len(result.points) == 11
    assert {point.type for point in result.points} == TERM_TYPES
    assert result.points[0].value == result.points[-1].value == "Archives"
    assert result.points[1].value == "García, José"
    assert all(point.finding_aid_id == "00042" for point in result.points)
    assert all(point.finding_aid_title == "García & Sons 1900-1910" for point in result.points)
    assert all(point.source_file == str((FIXTURES / "ead2002.xml").resolve()) for point in result.points)


def test_ead3_parts_and_coordinates():
    result = extract_file(FIXTURES / "ead3.xml")
    assert not result.issues
    assert result.version == "EAD3"
    assert [point.value for point in result.points] == ["Archives | Texas", "Lubbock"]
    assert result.points[0].finding_aid_id == "E3-0007"
    assert result.points[0].finding_aid_title == "EAD3 collection 1945-1950"


@pytest.mark.parametrize("version", ["EAD2002", "EAD3"])
@pytest.mark.parametrize("namespace", [True, False])
def test_versions_and_no_namespace(tmp_path, version, namespace):
    term = "<subject>A</subject>" if version == "EAD2002" else "<subject><part>A</part></subject>"
    result = run_xml(tmp_path, document(f"<controlaccess>{term}</controlaccess>", version=version, namespace=namespace))
    assert not result.issues
    assert result.version == version
    assert result.points[0].value == "A"


def test_prefix_independence(tmp_path):
    xml = f'<x:ead xmlns:x="{EAD2002_NS}"><x:eadheader><x:eadid>01</x:eadid></x:eadheader><x:archdesc><x:did><x:unittitle>T</x:unittitle></x:did><x:controlaccess><x:subject>A</x:subject></x:controlaccess></x:archdesc></x:ead>'
    result = run_xml(tmp_path, xml)
    assert not result.issues
    assert result.points[0].value == "A"


def test_nested_inline_punctuation_breaks_comments_and_headings(tmp_path):
    xml = document('<controlaccess><head><title>Not a term</title></head><subject>A<emph>B</emph>,<lb/>C<!--not text-->D<?test ignored?>E<title>!</title></subject><controlaccess><persname>Other</persname></controlaccess></controlaccess><subject>Outside</subject>')
    result = run_xml(tmp_path, xml)
    assert not result.issues
    assert [(point.type, point.value) for point in result.points] == [("subject", "AB, CDE!"), ("persname", "Other")]


def test_component_controls_and_true_duplicates(tmp_path):
    result = run_xml(tmp_path, document('<dsc><c01><controlaccess><subject>A</subject><subject>A</subject></controlaccess></c01></dsc>'))
    assert len(result.points) == 2
    assert not result.issues


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be", "iso-8859-1"])
def test_declared_encodings(tmp_path, encoding):
    declaration = "UTF-16" if encoding == "utf-16" else encoding.replace("16-", "16")
    xml = f'<?xml version="1.0" encoding="{declaration}"?>' + document('<controlaccess><subject>Café</subject></controlaccess>')
    result = run_xml(tmp_path, xml, encoding=encoding)
    assert not result.issues
    assert result.points[0].value == "Café"


def test_missing_metadata_empty_and_unknown_preserve_known(tmp_path):
    result = run_xml(tmp_path, document('<controlaccess><unknown>Do not export</unknown><subject> </subject><subject>Keep <unfamiliar>this</unfamiliar></subject></controlaccess>', metadata=False))
    assert not result.failed
    assert [point.value for point in result.points] == ["Keep this"]
    assert result.points[0].finding_aid_id == result.points[0].finding_aid_title == ""
    assert "Missing finding aid ID" in problems(result)
    assert "Missing finding aid title" in problems(result)
    assert "Empty <subject>" in problems(result)
    assert "Unfamiliar" in problems(result)
    assert all("line" in issue.problem for issue in result.issues if "Missing" not in issue.problem)


def test_collection_title_without_header_metadata(tmp_path):
    result = run_xml(tmp_path, document('<did><unittitle>Fallback <emph>title</emph></unittitle></did><controlaccess><subject>A</subject></controlaccess>', metadata=False))
    assert result.points[0].finding_aid_title == "Fallback title"
    assert "Missing finding aid title" not in problems(result)


def test_no_control_sections(tmp_path):
    result = run_xml(tmp_path, document(""))
    assert not result.failed
    assert not result.points
    assert "No <controlaccess>" in problems(result)


@pytest.mark.parametrize("xml", ["<html/>", '<ead xmlns="urn:unknown"/>', "<ead><archdesc/></ead>", "<ead><eadheader/><control/></ead>", f'<ead xmlns="{EAD2002_NS}"><control/></ead>', f'<ead xmlns="{EAD3_NS}"><eadheader/></ead>'])
def test_unsupported_and_ambiguous_xml(tmp_path, xml):
    result = run_xml(tmp_path, xml)
    assert result.failed
    assert not result.points


def test_malformed_reports_position():
    result = extract_file(FIXTURES / "malformed.xml")
    assert result.failed
    assert "line 1, column" in problems(result)


def test_unreadable_file(tmp_path):
    result = extract_file(tmp_path / "missing.xml")
    assert result.failed
    assert "Cannot read XML" in problems(result)


def test_doctype_alone_does_not_load_network(tmp_path):
    result = run_xml(tmp_path, '<!DOCTYPE ead SYSTEM "https://invalid.invalid/never.dtd">' + document('<controlaccess><subject>A &amp; B</subject></controlaccess>'))
    assert not result.issues
    assert result.points[0].value == "A & B"


@pytest.mark.parametrize("entity", ['<!ENTITY secret SYSTEM "file:///C:/Windows/win.ini">', '<!ENTITY secret "injected">'])
def test_unresolved_entities_rejected(tmp_path, entity):
    result = run_xml(tmp_path, f'<!DOCTYPE ead [{entity}]>' + document('<controlaccess><subject>&secret;</subject></controlaccess>'))
    assert result.failed
    assert not result.points
    assert "Unresolved entity" in problems(result)


def test_undeclared_entity_rejected(tmp_path):
    result = run_xml(tmp_path, document('<controlaccess><subject>&missing;</subject></controlaccess>'))
    assert result.failed


def test_foreign_namespaces_do_not_create_false_terms(tmp_path):
    result = run_xml(tmp_path, document('<controlaccess xmlns:other="urn:other"><other:subject>Foreign</other:subject><subject>Real</subject></controlaccess>'))
    assert [point.value for point in result.points] == ["Real"]
    assert "Unfamiliar" in problems(result)


def test_ead3_unknown_structure_and_empty_parts(tmp_path):
    result = run_xml(tmp_path, document('<controlaccess><subject><part> </part><part>Keep</part></subject><persname>Raw name</persname><geogname><geographiccoordinates>55,44</geographiccoordinates></geogname></controlaccess>', version="EAD3"))
    assert not result.failed
    assert [point.value for point in result.points] == ["Keep", "Raw name"]
    assert "Empty <part>" in problems(result)
    assert "expected <part>" in problems(result)
    assert "Empty <geogname>" in problems(result)


def test_all_ead3_types_and_inline_part(tmp_path):
    content = "".join(f'<{typ}><part>A<emph>B</emph>,<lb/>C</part><part>D</part></{typ}>' for typ in sorted(TERM_TYPES))
    result = run_xml(tmp_path, document(f"<controlaccess>{content}</controlaccess>", version="EAD3"))
    assert not result.issues
    assert len(result.points) == 10
    assert all(point.value == "AB, C | D" for point in result.points)


@pytest.mark.parametrize("version", ["EAD2002", "EAD3"])
@pytest.mark.parametrize("namespace", [True, False])
@pytest.mark.parametrize("term_type", sorted(TERM_TYPES))
def test_source_attribute_for_every_control_point_type(tmp_path, version, namespace, term_type):
    value = "<part>Same term</part>" if version == "EAD3" else "Same term"
    terms = "".join(
        f'<{term_type}{attribute}>{value}</{term_type}>'
        for attribute in (' source="fast"', ' source="lcsh"', "", ' source=""')
    )
    result = run_xml(tmp_path, document(
        f"<controlaccess>{terms}</controlaccess>", version=version, namespace=namespace,
    ))
    assert not result.issues
    assert [(point.type, point.source, point.value) for point in result.points] == [
        (term_type, source, "Same term") for source in ("fast", "lcsh", "", "")
    ]


@pytest.mark.parametrize("version", ["EAD2002", "EAD3"])
@pytest.mark.parametrize("namespace", [True, False])
def test_source_comes_only_from_the_control_point_element(tmp_path, version, namespace):
    child = "part" if version == "EAD3" else "emph"
    content = f'''<controlaccess source="parent">
<subject><{child} source="child">No local source</{child}></subject>
<subject source=""><{child} source="child">Empty local source</{child}></subject>
<subject source=" FAST "><{child} source="child">Own source</{child}></subject>
</controlaccess>'''
    result = run_xml(tmp_path, document(content, version=version, namespace=namespace))
    assert not result.issues
    assert [(point.source, point.value) for point in result.points] == [
        ("", "No local source"), ("", "Empty local source"), (" FAST ", "Own source"),
    ]


@pytest.mark.parametrize("version", ["EAD2002", "EAD3"])
@pytest.mark.parametrize("raw_id,expected", [
    ("urn:taro:ttu.sw.00001", "00001"),
    ("organization.collection.section.000042", "000042"),
    ("EAD-0042", "EAD-0042"),
    ("collection. 0007 ", "0007"),
    ("collection.", ""),
])
def test_finding_aid_id_uses_only_final_dot_segment(tmp_path, version, raw_id, expected):
    term = "<subject>A</subject>" if version == "EAD2002" else "<subject><part>A</part></subject>"
    ident = "eadid" if version == "EAD2002" else "recordid"
    xml = document(f"<controlaccess>{term}</controlaccess>", version=version)
    xml = xml.replace(f"<{ident}>0001</{ident}>", f"<{ident}>{raw_id}</{ident}>")
    result = run_xml(tmp_path, xml)
    assert result.points[0].finding_aid_id == expected
    assert ("Missing finding aid ID" in problems(result)) == (not expected)


def collection_document(did_contents, *, version="EAD2002", extra=""):
    term = "<subject>A</subject>" if version == "EAD2002" else "<subject><part>A</part></subject>"
    return document(f"<controlaccess>{term}</controlaccess>{extra}", version=version).replace(
        "<did><unittitle>Collection title</unittitle></did>", f"<did>{did_contents}</did>"
    )


def test_collection_title_and_date_replace_header_title(tmp_path):
    result = run_xml(tmp_path, collection_document('<unittitle>García <emph>&amp; Sons</emph>, </unittitle><unitdate> 1900-1910 and undated </unitdate>'))
    assert not result.issues
    assert result.points[0].finding_aid_title == "García & Sons, 1900-1910 and undated"
    assert "Title" not in result.points[0].finding_aid_title


@pytest.mark.parametrize("sibling_dates", ["", "<unitdate>1987-1989</unitdate>", "<unitdate> 1987-1989 </unitdate><unitdate>1987-1989</unitdate>"])
def test_date_nested_in_unittitle_is_not_duplicated(tmp_path, sibling_dates):
    result = run_xml(tmp_path, collection_document('<unittitle>Steve Moss Collection, <unitdate>1987-1989</unitdate></unittitle>' + sibling_dates))
    assert not result.issues
    assert result.points[0].finding_aid_title == "Steve Moss Collection, 1987-1989"


def test_multiple_collection_dates_keep_document_order(tmp_path):
    result = run_xml(tmp_path, collection_document('<unittitle>Phoebe Lloyd Papers, </unittitle><unitdate type="inclusive">1880-2006</unitdate><unitdate type="bulk">1934-2006</unitdate>'))
    assert not result.issues
    assert result.points[0].finding_aid_title == "Phoebe Lloyd Papers, 1880-2006 1934-2006"


@pytest.mark.parametrize("title,date,expected,warn", [
    ("<unittitle>Collection</unittitle>", "", "Collection", False),
    ("", "<unitdate>1900-1910</unitdate>", "1900-1910", True),
    ("<unittitle> </unittitle>", "<unitdate> </unitdate>", "", True),
    ("", "", "", True),
])
def test_missing_title_or_date_join_cleanly_and_never_use_header(tmp_path, title, date, expected, warn):
    result = run_xml(tmp_path, collection_document(title + date))
    assert result.points[0].finding_aid_title == expected
    assert ("Missing finding aid title" in problems(result)) == warn


def test_component_titles_and_dates_do_not_change_collection_metadata(tmp_path):
    result = run_xml(tmp_path, collection_document('<unittitle>Root collection</unittitle><unitdate>1900</unitdate>', extra='<dsc><c01><did><unittitle>Component title</unittitle><unitdate>2000</unitdate></did><controlaccess><persname>Person</persname></controlaccess></c01></dsc>'))
    assert not result.issues
    assert len(result.points) == 2
    assert {point.finding_aid_title for point in result.points} == {"Root collection 1900"}


def test_ead3_structured_date_range_set_and_human_text(tmp_path):
    xml = collection_document('<unittitle>Date collection</unittitle><unitdatestructured><dateset><daterange><fromdate standarddate="1990-01-01">1 January <emph>1990</emph></fromdate><todate standarddate="1995-12-31">31 December 1995</todate></daterange><datesingle standarddate="2000">circa 2000</datesingle></dateset></unitdatestructured>', version="EAD3")
    result = run_xml(tmp_path, xml)
    assert not result.issues
    assert result.points[0].finding_aid_title == "Date collection 1 January 1990 - 31 December 1995; circa 2000"


def test_ead3_structured_date_attributes_do_not_invent_missing_text(tmp_path):
    result = run_xml(tmp_path, collection_document('<unittitle>Date collection</unittitle><unitdatestructured><datesingle standarddate="1990"/></unitdatestructured>', version="EAD3"))
    assert not result.issues
    assert result.points[0].finding_aid_title == "Date collection"
