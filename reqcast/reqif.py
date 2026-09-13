"""Serializes an extracted Node tree to ReqIF 1.2 XML and packages a .reqifz.

Follows the same conventions as this repo's other hand-rolled ReqIF
generators (see tuning-datasets/dataset-011-eclipse-score/convert/reqif.py):
- the source document's own identifier goes in SPEC-OBJECT/@LONG-NAME and in
  a ReqIF.ForeignID string attribute
- the free-text body is an ATTRIBUTE-VALUE-XHTML under an attribute
  definition named "ReqIF.Text", which is the name the importer looks for
- figures are linked by default, not embedded inline as base64: each is an
  <xhtml:object data="media/..." type="image/png"> pointing at a real file
  bundled inside the .reqifz archive at that same relative path. Pass
  embed_images=True to inline each figure as a base64 data URI instead
  (`<xhtml:object data="data:image/png;base64,...">`), per the ReqIF spec's
  own allowance for embedded binary data - useful for a single
  self-contained .reqif with no media/ sidecar.
"""
from __future__ import annotations

import base64
import dataclasses
import datetime
import pathlib
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from .extract import Node

REQIF_NS = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"
XHTML_NS = "http://www.w3.org/1999/xhtml"

# XML 1.0 forbids most C0/C1 control characters, even as numeric character
# references. PDF text extraction occasionally yields them (a font's
# private-use glyph, an OCR artifact) - strip rather than let one bad
# codepoint invalidate the whole document.
_XML_ALLOWED_RANGES = [
    (0x09, 0x09), (0x0A, 0x0A), (0x0D, 0x0D),
    (0x20, 0xD7FF), (0xE000, 0xFFFD), (0x10000, 0x10FFFF),
]
_XML_ILLEGAL_RE = re.compile(
    "[^" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _XML_ALLOWED_RANGES) + "]"
)


def _xml_safe(text: str) -> str:
    return _XML_ILLEGAL_RE.sub("", text)


def _attr(name: str, value: str) -> str:
    return f" {name}={quoteattr(_xml_safe(value))}"


def _text(value: str) -> str:
    return escape(_xml_safe(value))


class ReqIfWriter:
    def __init__(self, title: str, source_tool_id: str = "reqcast", embed_images: bool = False) -> None:
        self.title = title
        self.source_tool_id = source_tool_id
        self.embed_images = embed_images
        self.stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.lines: list[str] = []
        self._hierarchy_counter = 0
        self._assets: dict[str, bytes] = {}

    def _open(self, tag: str, **attrs: str) -> None:
        self.lines.append(f"<{tag}{''.join(_attr(k.replace('_', '-'), v) for k, v in attrs.items())}>")

    def _close(self, tag: str) -> None:
        self.lines.append(f"</{tag}>")

    def _leaf(self, tag: str, text: str = "", **attrs: str) -> None:
        rendered = "".join(_attr(k.replace("_", "-"), v) for k, v in attrs.items())
        self.lines.append(f"<{tag}{rendered}>{text}</{tag}>" if text else f"<{tag}{rendered}/>")

    def _ref(self, wrapper: str, tag: str, target: str) -> None:
        self.lines.append(f"<{wrapper}><{tag}>{target}</{tag}></{wrapper}>")

    def _header(self) -> None:
        self._open("THE-HEADER")
        self._open("REQ-IF-HEADER", IDENTIFIER="RIH-1")
        self._leaf("CREATION-TIME", self.stamp)
        self._leaf("REQ-IF-TOOL-ID", self.source_tool_id)
        self._leaf("REQ-IF-VERSION", "1.0")
        self._leaf("SOURCE-TOOL-ID", self.source_tool_id)
        self._leaf("TITLE", _text(self.title))
        self._close("REQ-IF-HEADER")
        self._close("THE-HEADER")

    def _datatypes(self) -> None:
        self._open("DATATYPES")
        self._leaf("DATATYPE-DEFINITION-STRING", IDENTIFIER="DT-STRING",
                   LAST_CHANGE=self.stamp, LONG_NAME="String", MAX_LENGTH="32000")
        self._leaf("DATATYPE-DEFINITION-XHTML", IDENTIFIER="DT-XHTML",
                   LAST_CHANGE=self.stamp, LONG_NAME="XHTML")
        self._close("DATATYPES")

    def _build_attr_ids(self, requirement_attrs: list[str]) -> dict[tuple[str, str], str]:
        """Two distinct discovered labels can sanitize to the same identifier
        (e.g. "Owner-Team" and "Owner Team" both -> AD-Requirement-Owner_Team,
        since label_pattern permits '-', '_', and ' ' interchangeably). Assign
        every (type_key, name) pair actually used in this document a
        guaranteed-unique id up front, rather than resolving punctuation
        per-call and risking two different attributes silently sharing one
        ATTRIBUTE-DEFINITION-STRING identifier in the output."""
        ids: dict[tuple[str, str], str] = {}

        def register(type_key: str, names: list[str]) -> None:
            used: set[str] = set()
            for name in names:
                safe = "".join(c if c.isalnum() else "_" for c in name)
                base = f"AD-{type_key}-{safe}"
                candidate, n = base, 2
                while candidate in used:
                    candidate = f"{base}_{n}"
                    n += 1
                used.add(candidate)
                ids[(type_key, name)] = candidate

        register("Heading", ["ReqIF.ForeignID", "ReqIF.Name", "ReqIF.ChapterName", "ReqIF.Text"])
        register("Requirement", ["ReqIF.ForeignID", "ReqIF.Name", *requirement_attrs, "ReqIF.Text"])
        return ids

    def _ad_id(self, type_key: str, attr_name: str) -> str:
        return self._attr_ids[(type_key, attr_name)]

    def _spec_object_type(self, type_key: str, string_attrs: list[str], has_text: bool) -> None:
        self._open("SPEC-OBJECT-TYPE", IDENTIFIER=f"SOT-{type_key}",
                   LAST_CHANGE=self.stamp, LONG_NAME=type_key)
        self._open("SPEC-ATTRIBUTES")
        for name in string_attrs:
            self._open("ATTRIBUTE-DEFINITION-STRING", IDENTIFIER=self._ad_id(type_key, name),
                       LAST_CHANGE=self.stamp, LONG_NAME=name)
            self._ref("TYPE", "DATATYPE-DEFINITION-STRING-REF", "DT-STRING")
            self._close("ATTRIBUTE-DEFINITION-STRING")
        if has_text:
            self._open("ATTRIBUTE-DEFINITION-XHTML", IDENTIFIER=self._ad_id(type_key, "ReqIF.Text"),
                       LAST_CHANGE=self.stamp, LONG_NAME="ReqIF.Text")
            self._ref("TYPE", "DATATYPE-DEFINITION-XHTML-REF", "DT-XHTML")
            self._close("ATTRIBUTE-DEFINITION-XHTML")
        self._close("SPEC-ATTRIBUTES")
        self._close("SPEC-OBJECT-TYPE")

    def _spec_types(self, requirement_attrs: list[str]) -> None:
        self._open("SPEC-TYPES")
        self._spec_object_type("Heading", ["ReqIF.ForeignID", "ReqIF.Name", "ReqIF.ChapterName"], has_text=True)
        self._spec_object_type("Requirement", ["ReqIF.ForeignID", "ReqIF.Name", *requirement_attrs], has_text=True)
        self._open("SPECIFICATION-TYPE", IDENTIFIER="SPT-1", LAST_CHANGE=self.stamp, LONG_NAME="Specification")
        self._open("SPEC-ATTRIBUTES")
        self._close("SPEC-ATTRIBUTES")
        self._close("SPECIFICATION-TYPE")
        self._close("SPEC-TYPES")

    def _xhtml_body(self, node: Node) -> str:
        parts = []
        if node.body:
            for para in node.body.split("\n"):
                para = para.strip()
                if para:
                    parts.append(f"<xhtml:p>{_text(para)}</xhtml:p>")
        for fig in node.figures:
            alt = _text(fig.caption or "figure")
            if self.embed_images:
                b64 = base64.b64encode(fig.image_png).decode("ascii")
                data_uri = f"data:image/png;base64,{b64}"
                parts.append(
                    f'<xhtml:p><xhtml:object data={quoteattr(data_uri)} '
                    f'type="image/png">{alt}</xhtml:object></xhtml:p>'
                )
            else:
                asset_name = f"media/{node.identifier}-fig{fig.order:02d}.png".replace(" ", "_")
                self._assets[asset_name] = fig.image_png
                parts.append(
                    f'<xhtml:p><xhtml:object data={quoteattr(_xml_safe(asset_name))} '
                    f'type="image/png">{alt}</xhtml:object></xhtml:p>'
                )
            if fig.caption:
                parts.append(f"<xhtml:p><xhtml:em>{_text(fig.caption)}</xhtml:em></xhtml:p>")
        if not parts:
            return ""
        return f"<xhtml:div>{''.join(parts)}</xhtml:div>"

    def _values(self, type_key: str, string_values: dict[str, str], xhtml: str) -> None:
        self._open("VALUES")
        for name, value in string_values.items():
            if value is None:
                continue
            self._open("ATTRIBUTE-VALUE-STRING", THE_VALUE=_truncate(value))
            self._ref("DEFINITION", "ATTRIBUTE-DEFINITION-STRING-REF", self._ad_id(type_key, name))
            self._close("ATTRIBUTE-VALUE-STRING")
        if xhtml:
            self._open("ATTRIBUTE-VALUE-XHTML")
            self._ref("DEFINITION", "ATTRIBUTE-DEFINITION-XHTML-REF", self._ad_id(type_key, "ReqIF.Text"))
            self.lines.append(f"<THE-VALUE>{xhtml}</THE-VALUE>")
            self._close("ATTRIBUTE-VALUE-XHTML")
        self._close("VALUES")

    def _spec_object(self, node: Node, safe_id: str) -> None:
        type_key = "Heading" if node.kind == "heading" else "Requirement"
        string_values = {"ReqIF.ForeignID": node.identifier, "ReqIF.Name": node.title}
        if node.kind == "heading":
            string_values["ReqIF.ChapterName"] = node.title
        else:
            string_values.update(node.attributes)
        xhtml = self._xhtml_body(node) if (node.kind == "requirement" or node.figures) else ""
        self._open("SPEC-OBJECT", IDENTIFIER=safe_id, LAST_CHANGE=self.stamp,
                   LONG_NAME=node.identifier)
        self._values(type_key, string_values, xhtml)
        self._ref("TYPE", "SPEC-OBJECT-TYPE-REF", f"SOT-{type_key}")
        self._close("SPEC-OBJECT")

    def _hierarchy(self, node: Node, safe_id: str) -> None:
        self._hierarchy_counter += 1
        attrs = {"LONG_NAME": node.title} if node.kind == "heading" and node.title else {}
        self._open("SPEC-HIERARCHY", IDENTIFIER=f"SH-{self._hierarchy_counter:06d}",
                   LAST_CHANGE=self.stamp, **attrs)
        self._ref("OBJECT", "SPEC-OBJECT-REF", safe_id)
        if node.children:
            self._open("CHILDREN")
            for child in node.children:
                self._hierarchy(child, self._ids[id(child)])
            self._close("CHILDREN")
        self._close("SPEC-HIERARCHY")

    def _specification(self, roots: list[Node]) -> None:
        self._open("SPECIFICATIONS")
        self._open("SPECIFICATION", IDENTIFIER="SPEC-1", LAST_CHANGE=self.stamp,
                   LONG_NAME=self.title)
        self._ref("TYPE", "SPECIFICATION-TYPE-REF", "SPT-1")
        self._open("CHILDREN")
        for root in roots:
            self._hierarchy(root, self._ids[id(root)])
        self._close("CHILDREN")
        self._close("SPECIFICATION")
        self._close("SPECIFICATIONS")

    def render(self, roots: list[Node]) -> str:
        flat = list(_walk(roots))
        self._ids = {id(node): f"SO-{i + 1:06d}" for i, node in enumerate(flat)}
        requirement_attrs = sorted({
            label for node in flat if node.kind == "requirement" for label in node.attributes
        })
        self._attr_ids = self._build_attr_ids(requirement_attrs)

        self.lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        self.lines.append(f'<REQ-IF xmlns="{REQIF_NS}" xmlns:xhtml="{XHTML_NS}">')
        self._header()
        self._open("CORE-CONTENT")
        self._open("REQ-IF-CONTENT")
        self._datatypes()
        self._spec_types(requirement_attrs)
        self._open("SPEC-OBJECTS")
        for node in flat:
            self._spec_object(node, self._ids[id(node)])
        self._close("SPEC-OBJECTS")
        self._specification(roots)
        self._close("REQ-IF-CONTENT")
        self._close("CORE-CONTENT")
        self._close("REQ-IF")
        return "\n".join(self.lines)

    def package(self, reqif_xml: str, out_path: pathlib.Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        inner_name = out_path.stem + ".reqif"
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(inner_name, reqif_xml)
            for archive_name, data in sorted(self._assets.items()):
                archive.writestr(archive_name, data)

    def write_reqif(self, reqif_xml: str, out_path: pathlib.Path) -> None:
        """Write a plain, unzipped .reqif file instead of a .reqifz package.

        With embed_images=False, any linked figures still need to land
        somewhere real: they're written to a media/ directory next to
        out_path, so the <xhtml:object data="media/..."> references resolve
        the same way they would inside a .reqifz archive.
        """
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(reqif_xml, encoding="utf-8")
        for archive_name, data in sorted(self._assets.items()):
            asset_path = out_path.parent / archive_name
            asset_path.parent.mkdir(parents=True, exist_ok=True)
            asset_path.write_bytes(data)


def _walk(nodes: list[Node]):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _truncate(value: str, limit: int = 32000) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."
