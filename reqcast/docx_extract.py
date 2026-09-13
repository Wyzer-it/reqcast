"""Format-native extraction for Word .docx files.

Uses python-docx's real paragraph objects: a paragraph styled "Heading N"
is a heading of depth N (falling back to `cfg.heading_pattern` only for
numbered headings that were typed as plain text rather than styled), and
embedded images are read directly off each paragraph's own XML rather
than the geometric page-region cropping the PDF path needs.

Word tables are not read - only the paragraph flow. `cfg.manual_figures`
(keyed to a PDF page/slice/bbox) does not apply to this format.

Needs the `python-docx` package - imported lazily so installing reqcast
for PDF-only or text-only use doesn't require it.
"""
from __future__ import annotations

import pathlib
import re

from .extract import Config, ExtractionReport, Node
from .node_builder import NodeBuilder

_HEADING_STYLE_RE = re.compile(r"^Heading\s*(\d+)$", re.IGNORECASE)


def extract_docx(path: pathlib.Path, cfg: Config) -> tuple[list[Node], ExtractionReport]:
    import docx
    from docx.oxml.ns import qn

    document = docx.Document(str(path))

    report = ExtractionReport(logical_pages=1)
    builder = NodeBuilder(cfg, report, join_paragraphs=True)
    figure_re = re.compile(cfg.figure_pattern)

    # An image paragraph and its caption paragraph are two separate elements
    # in document order (unlike Markdown's single-line ![]() or PDF's
    # geometric pairing), so a bare image or a bare caption has to wait here
    # until the other half of the pair shows up.
    pending_images: list[bytes] = []
    pending_caption: str | None = None

    def flush_pending() -> None:
        """Drop any image(s)/caption still waiting for their pair at a
        section boundary (a new requirement or heading) instead of letting
        them leak into the next node."""
        nonlocal pending_images, pending_caption
        for _ in pending_images:
            builder.flush_orphan_figure(pending_caption)
        pending_images = []
        if pending_caption is not None:
            builder.flush_orphan_figure(pending_caption)
            pending_caption = None

    def paragraph_images(paragraph) -> list[bytes]:
        out = []
        for blip in paragraph._p.findall(f".//{qn('a:blip')}"):
            rid = blip.get(qn("r:embed"))
            if not rid:
                continue
            try:
                out.append(paragraph.part.related_parts[rid].blob)
            except KeyError:
                continue
        return out

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        style_name = (paragraph.style.name or "") if paragraph.style else ""
        heading_style_match = _HEADING_STYLE_RE.match(style_name.strip())
        images = paragraph_images(paragraph)

        if not text and not images:
            builder.flush_para()
            continue

        id_match = builder.match_id(text) if text else None
        heading_match = builder.match_heading(text) if (not heading_style_match and text) else None
        figure_match = figure_re.match(text) if (text and not images) else None
        label_match = builder.match_label(text, blocked=not text or bool(
            id_match or heading_style_match or heading_match or figure_match
        )) if text else None

        if id_match:
            flush_pending()
            title = id_match.groupdict().get("title")
            if title is None:
                title = text[id_match.end():].strip(" \t-:")
            builder.attach_requirement(id_match.group("id"), title)
            continue

        if heading_style_match or heading_match:
            flush_pending()
            if heading_style_match:
                depth = int(heading_style_match.group(1))
                number, title = str(depth), text
            else:
                number = heading_match.group("number")
                depth = number.count(".") + 1
                title = heading_match.group("title")
            builder.attach_heading(number, depth, title)
            continue

        if images:
            builder.flush_para()
            if cfg.figure_caption_position == "above" and pending_caption is not None:
                for png in images:
                    builder.attach_figure(png, pending_caption)
                pending_caption = None
            else:
                pending_images.extend(images)
            continue

        if figure_match:
            builder.flush_para()
            caption = figure_match.group("caption").strip()
            if cfg.figure_caption_position == "below" and pending_images:
                for png in pending_images:
                    builder.attach_figure(png, caption)
                pending_images = []
            elif cfg.figure_caption_position == "above":
                pending_caption = caption
            else:
                builder.attach_figure(None, caption)
            continue

        if label_match:
            builder.record_label(label_match.group("label").strip(), label_match.group("value").strip())
            continue

        if text:
            builder.append_body(text)

    flush_pending()
    builder.finish()
    return builder.roots, report
