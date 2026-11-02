"""Namespace-aware, offline EAD2002/EAD3 extraction.

This intentionally reads only controlled access terms. It does not infer names
or subjects from narrative prose, headings, or collection descriptions.
"""
from pathlib import Path

from lxml import etree

from .models import ControlPoint, ExtractionResult, Issue

EAD2002_NS = "urn:isbn:1-931666-22-9"
EAD3_NS = "http://ead3.archivists.org/schema/"
TERM_TYPES = frozenset({
    "subject", "persname", "corpname", "geogname", "famname", "title",
    "genreform", "function", "occupation", "name",
})
# Legal structural and inline markup may contain controlled access terms.
_STRUCTURAL = frozenset({
    "controlaccess", "head", "p", "blockquote", "chronlist", "chronitem",
    "event", "eventgrp", "list", "listhead", "item", "defitem", "label",
    "table", "tgroup", "colspec", "spanspec", "thead", "tbody", "tfoot",
    "row", "entry", "address", "addressline", "note", "listitem",
})
_INLINE = frozenset({
    "abbr", "date", "emph", "expan", "foreign", "lb", "ptr", "ref",
    "extptr", "extref", "num", "subarea", "edition", "imprint", "bibref",
})


def _name(element: etree._Element) -> tuple[str, str]:
    if not isinstance(element.tag, str):
        return "", ""
    name = etree.QName(element)
    return name.namespace or "", name.localname


def _text(element: etree._Element, *, exclude_coordinates: bool = False) -> str:
    """Keep inline punctuation; comments/processing instructions are not text."""
    chunks: list[str] = []

    def visit(node: etree._Element) -> None:
        _, local = _name(node)
        if not isinstance(node.tag, str):
            return
        if exclude_coordinates and local == "geographiccoordinates":
            return
        if local == "lb":
            chunks.append(" ")
        if node.text:
            chunks.append(node.text)
        for child in node:
            visit(child)
            if child.tail:
                chunks.append(child.tail)

    visit(element)
    return " ".join("".join(chunks).split())


def extract_file(path: Path | str) -> ExtractionResult:
    """Return points/issues for one file; normal input failures are records.

    No DTD, network resource or external/internal entity is expanded. Entity
    references left unresolved by that policy fail the file, preventing partial
    text from being presented as a faithful extraction.
    """
    source = str(Path(path).resolve())
    result = ExtractionResult(source_file=source)

    def issue(severity: str, problem: str, element: etree._Element | None = None) -> None:
        if element is not None and element.sourceline:
            problem += f" (line {element.sourceline})"
        result.issues.append(Issue(source, severity, problem))

    parser = etree.XMLParser(
        resolve_entities=False, load_dtd=False, no_network=True,
        recover=False, huge_tree=False,
    )
    try:
        tree = etree.parse(source, parser)
    except etree.XMLSyntaxError as exc:
        line, column = exc.position
        issue("Error", f"Malformed XML at line {line}, column {column}: {exc.msg}")
        return result
    except (OSError, ValueError) as exc:
        issue("Error", f"Cannot read XML: {exc}")
        return result

    root = tree.getroot()
    if any(isinstance(node, etree._Entity) for node in root.iter()):
        issue("Error", "Unresolved entity reference; entity expansion is disabled.")
        return result
    namespace, root_name = _name(root)
    if root_name != "ead":
        issue("Error", "Unsupported document: expected an <ead> root.", root)
        return result
    if namespace not in {"", EAD2002_NS, EAD3_NS}:
        issue("Error", f"Unsupported EAD namespace: {namespace}", root)
        return result

    def tag(local: str) -> str:
        return f"{{{namespace}}}{local}" if namespace else local

    old_header = root.find(tag("eadheader"))
    new_header = root.find(tag("control"))
    if old_header is not None and new_header is not None:
        issue("Error", "Ambiguous EAD structure: both <eadheader> and <control> are present.", root)
        return result
    if namespace == EAD2002_NS:
        if new_header is not None:
            issue("Error", "EAD2002 namespace conflicts with an EAD3 <control> header.", root)
            return result
        version = "EAD2002"
    elif namespace == EAD3_NS:
        if old_header is not None:
            issue("Error", "EAD3 namespace conflicts with an EAD2002 <eadheader>.", root)
            return result
        version = "EAD3"
    elif old_header is not None:
        version = "EAD2002"
    elif new_header is not None:
        version = "EAD3"
    else:
        issue("Error", "Cannot identify unnamespaced EAD version: expected <eadheader> or <control>.", root)
        return result
    result.version = version

    def metadata(parts: tuple[str, ...]) -> str:
        node = root.find("/".join(tag(part) for part in parts))
        return _text(node) if node is not None else ""

    header = "eadheader" if version == "EAD2002" else "control"
    aid_id = metadata((header, "eadid" if version == "EAD2002" else "recordid"))
    aid_id = aid_id.rsplit(".", 1)[-1].strip()

    def date_text(node: etree._Element) -> str:
        """Render human-readable EAD3 date components, never date attributes."""
        _, local = _name(node)
        if local == "daterange":
            return " - ".join(
                value for child in node
                if _name(child) in {(namespace, "fromdate"), (namespace, "todate")}
                if (value := _text(child))
            )
        if local in {"unitdatestructured", "dateset"}:
            return "; ".join(
                value for child in node
                if _name(child) in {
                    (namespace, "datesingle"), (namespace, "daterange"),
                    (namespace, "dateset"),
                }
                if (value := date_text(child))
            )
        return _text(node)

    did = root.find(f"{tag('archdesc')}/{tag('did')}")
    title_node = did.find(tag("unittitle")) if did is not None else None
    collection_title = _text(title_node) if title_node is not None else ""
    date_names = {"unitdate", "unitdatestructured"} if version == "EAD3" else {"unitdate"}
    # EAD2002 may embed the date in unittitle as well as providing a sibling
    # unitdate. Keep the title's complete text and append that date only once.
    seen_dates = {
        date_text(node) for node in title_node.iterdescendants()
        if _name(node)[0] == namespace and _name(node)[1] in date_names
    } if title_node is not None else set()
    title_parts = [collection_title] if collection_title else []
    if did is not None:
        for node in did:
            node_ns, local = _name(node)
            if node_ns == namespace and local in date_names:
                value = date_text(node)
                if value and value not in seen_dates:
                    title_parts.append(value)
                    seen_dates.add(value)
    aid_title = " ".join(title_parts)
    if not aid_id:
        issue("Warning", "Missing finding aid ID.")
    if not collection_title:
        issue("Warning", "Missing finding aid title: collection <unittitle> is missing or empty.")

    controls = 0

    def term_value(node: etree._Element) -> str:
        if version == "EAD3":
            parts = [child for child in node if _name(child) == (namespace, "part")]
            if parts:
                values = [_text(part, exclude_coordinates=True) for part in parts]
                if any(not value for value in values):
                    issue("Warning", "Empty <part> in control point.", node)
                value = " | ".join(value for value in values if value)
                if (node.text and node.text.strip()) or any(child.tail and child.tail.strip() for child in node):
                    issue("Warning", "Unfamiliar EAD3 term structure: text outside <part> was excluded.", node)
                for part in parts:
                    for child in part.iterdescendants():
                        child_ns, local = _name(child)
                        if local and (child_ns != namespace or local not in _INLINE):
                            issue("Warning", f"Unfamiliar EAD3 <part> child <{local}>.", child)
            else:
                issue("Warning", "Unfamiliar EAD3 term structure: expected <part>; used available text.", node)
                value = _text(node, exclude_coordinates=True)
            allowed = {"part", "geographiccoordinates"}
            for child in node:
                child_ns, local = _name(child)
                if local and (child_ns != namespace or local not in allowed):
                    issue("Warning", f"Unfamiliar EAD3 control point child <{local}>.", child)
            return value
        for child in node.iterdescendants():
            child_ns, local = _name(child)
            if local and (child_ns != namespace or local not in _INLINE | TERM_TYPES):
                issue("Warning", f"Unfamiliar control point child <{local}>.", child)
        return _text(node)

    def walk(node: etree._Element, active: bool = False) -> None:
        nonlocal controls
        node_ns, local = _name(node)
        if not local:
            return
        if node_ns == namespace and local == "controlaccess":
            controls += 1
            active = True
        if active and node_ns == namespace and local in TERM_TYPES:
            value = term_value(node)
            if value:
                result.points.append(ControlPoint(local, value, aid_id, aid_title, source))
            else:
                issue("Warning", f"Empty <{local}> control point was skipped.", node)
            return  # Inline terms are part of this occurrence, not extra rows.
        if active and local != "controlaccess":
            if node_ns != namespace or local not in _STRUCTURAL | _INLINE:
                issue("Warning", f"Unfamiliar controlaccess element <{local}>; plain text was not exported.", node)
            if node_ns == namespace and local == "head":
                return  # A heading can itself contain inline title/name markup.
        for child in node:
            walk(child, active)

    walk(root)
    if not controls:
        issue("Warning", "No <controlaccess> sections found.")
    return result
