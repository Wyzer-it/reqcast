"""Format-native extraction for plain text and Markdown.

Shares the same NodeBuilder core as `extract.py`'s PDF path and
`docx_extract.py` (see node_builder.py) but drops everything that only
makes sense for a PDF's page geometry: row merging, header/footer margin
bands, boilerplate stripping, and geometric figure cropping. Neither
format has "pages" in that sense.

Markdown gets two things PDF and plain text don't: native `#`..`######`
headings (used instead of `cfg.heading_pattern` when present) and native
`![alt](path)` images, read straight off disk and attached with the alt
text as caption - no `figure_pattern`/geometry pairing needed since the
image and its caption are already the same line.

Both formats also fix the "PDF line-wrap became a paragraph break"
problem at the source instead of needing `--polish`: consecutive
non-blank lines are joined into one paragraph and only a blank line (or
a structural match - a new ID, heading, label, or figure) ends it, which
is what these formats' own paragraph convention actually means.
"""
from __future__ import annotations

import dataclasses
import pathlib
import re

from .extract import Config, ExtractionReport, Node
from .node_builder import NodeBuilder

_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_IMAGE_RE = re.compile(r"^!\[(?P<alt>[^\]]*)\]\((?P<path>[^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")


@dataclasses.dataclass
class _TextLine:
    text: str
    heading_depth: int | None = None  # native heading (markdown '#'); None -> fall back to cfg.heading_pattern
    is_image: bool = False  # this line is a markdown ![]() reference, regardless of whether it loaded
    image_png: bytes | None = None  # pre-converted to PNG; None if the file failed to load/convert
    image_caption: str | None = None  # caption for image_png, e.g. markdown alt text (may be empty string)


def _strip_list_marker(text: str) -> str:
    return _LIST_MARKER_RE.sub("", text, count=1)


def _run_line_pipeline(lines: list[_TextLine], cfg: Config) -> tuple[list[Node], ExtractionReport]:
    report = ExtractionReport(logical_pages=1)
    builder = NodeBuilder(cfg, report, join_paragraphs=True)
    figure_re = re.compile(cfg.figure_pattern)

    for line in lines:
        text = line.text.strip()

        if not text and not line.is_image:
            builder.flush_para()
            continue

        match_text = _strip_list_marker(text) if builder.match_id(_strip_list_marker(text)) else text
        id_match = builder.match_id(match_text)
        heading_match = builder.match_heading(text) if line.heading_depth is None else None
        figure_match = figure_re.match(text) if not line.is_image else None
        label_match = builder.match_label(text, blocked=bool(
            id_match or heading_match or line.heading_depth is not None or figure_match or line.is_image
        ))

        if id_match:
            title = id_match.groupdict().get("title")
            if title is None:
                title = match_text[id_match.end():].strip(" \t-:")
            builder.attach_requirement(id_match.group("id"), title)
            continue

        if line.heading_depth is not None or heading_match:
            if line.heading_depth is not None:
                depth = line.heading_depth
                number = str(depth)  # markdown has no real numbering; depth is all we get
                title = _MD_HEADING_RE.match(text).group(2).strip()
            else:
                number = heading_match.group("number")
                depth = number.count(".") + 1
                title = heading_match.group("title")
            builder.attach_heading(number, depth, title)
            continue

        if line.is_image:
            builder.attach_figure(line.image_png, line.image_caption or "")
            continue

        if figure_match:
            builder.attach_figure(None, figure_match.group("caption").strip())
            continue

        if label_match:
            builder.record_label(label_match.group("label").strip(), label_match.group("value").strip())
            continue

        builder.append_body(text)

    builder.finish()
    return builder.roots, report


def extract_txt(text: str, cfg: Config) -> tuple[list[Node], ExtractionReport]:
    """Plain text has no headings-by-symbol and no embedded images - a
    `figure_pattern` match is recorded as skipped (nothing to crop), and
    `cfg.heading_pattern` is the only way to detect section headings."""
    lines = [_TextLine(text=raw) for raw in text.splitlines()]
    return _run_line_pipeline(lines, cfg)


def extract_markdown(text: str, base_dir: pathlib.Path, cfg: Config) -> tuple[list[Node], ExtractionReport]:
    """`base_dir` is the markdown file's own directory - `![alt](path)`
    image paths are resolved relative to it. Images are converted to PNG
    if they aren't already (needs Pillow). A missing file, unsupported
    format, or failed conversion is reported the same as a figure the
    automatic heuristic couldn't crop: counted in `figures_found` and
    `figures_skipped`, never silently dropped into the body text."""
    lines: list[_TextLine] = []
    for raw in text.splitlines():
        heading_match = _MD_HEADING_RE.match(raw.strip())
        image_match = _MD_IMAGE_RE.match(raw.strip())
        if heading_match:
            lines.append(_TextLine(text=raw, heading_depth=len(heading_match.group(1))))
        elif image_match:
            png = _load_image_as_png(base_dir / image_match.group("path"))
            lines.append(_TextLine(text=raw, is_image=True, image_png=png, image_caption=image_match.group("alt")))
        else:
            lines.append(_TextLine(text=raw))
    return _run_line_pipeline(lines, cfg)


def _load_image_as_png(path: pathlib.Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return None
