"""Turns normalized logical pages into a tree of requirement/heading/figure nodes.

Every pattern used here is supplied by the caller's config - this module has
no knowledge of any particular document's vocabulary, module names, or field
labels. It only recognizes generic, widely-used conventions:

- an identifier pattern at the start of a line, optionally followed by a
  title on the same (row-merged) line
- a numbered section heading ("1.2.3 Title")
- "Label: value" attribute lines (the label text is discovered per document,
  never hardcoded)
- "Figure N ..." / "Table N ..." captions, whose associated graphic is
  cropped from the page and linked as an image
- boilerplate running headers/footers, detected generically by looking for
  text that repeats verbatim, at the same relative position, across most
  pages - not by matching any specific string
"""
from __future__ import annotations

import dataclasses
import re
from collections import Counter

from .layout import LogicalPage


@dataclasses.dataclass
class Figure:
    caption: str
    image_png: bytes
    order: int


@dataclasses.dataclass
class Node:
    kind: str  # "heading" | "requirement"
    identifier: str
    title: str
    attributes: dict  # discovered "Label" -> value, in first-seen order
    body_lines: list
    figures: list  # Figure
    children: list
    order: int

    @property
    def body(self) -> str:
        return "\n".join(self.body_lines).strip()


@dataclasses.dataclass
class ExtractionReport:
    logical_pages: int = 0
    requirements: int = 0
    headings: int = 0
    figures_found: int = 0
    figures_cropped: int = 0
    figures_skipped: list = dataclasses.field(default_factory=list)
    discovered_attribute_labels: Counter = dataclasses.field(default_factory=Counter)
    boilerplate_lines_dropped: int = 0


@dataclasses.dataclass
class Config:
    # Every pattern below must use named groups exactly as documented -
    # positional groups are never relied on, so callers can freely add
    # non-capturing groups of their own.
    id_pattern: str  # requires group 'id'; optional group 'title' for same-line title
    heading_pattern: str | None = r"^(?P<number>\d+(?:\.\d+){0,5})\s+(?P<title>[A-Z]\S.*)$"
    label_pattern: str = r"^(?P<label>[A-Z][A-Za-z0-9 ./_-]{1,40}):\s*(?P<value>.*)$"
    figure_pattern: str = r"^(?P<caption>(?:Figure|Table)\s+\d+[:.]?\s*.*)$"
    figure_caption_position: str = "below"  # or "above"
    # Which discovered label holds the requirement's free-text statement. When
    # unset, every discovered label's value is folded into the body in order,
    # which is a safe but less clean default for documents where no single
    # field carries the "the requirement" text.
    body_label: str | None = None
    row_merge_tolerance: float = 2.5
    header_margin_frac: float = 0.06
    footer_margin_frac: float = 0.18
    boilerplate_min_page_frac: float = 0.3
    figure_dpi: int = 200
    figure_padding: float = 6.0
    # Slack (points) between a figure/table's own bounding box and its
    # caption line - captions rarely sit flush against the graphic.
    figure_caption_gap: float = 12.0
    # How far below the top of a fresh page a figure-region search is allowed
    # to start looking, as a fraction of page height. Deliberately separate
    # from header_margin_frac: that one protects text-boilerplate detection
    # and often needs to be wide (a tall running banner); this one only needs
    # to be wide enough to skip that same banner's own graphics, and widening
    # it unnecessarily starts excluding real figures/tables that legitimately
    # sit near the top of a page with no preceding heading on that page.
    figure_top_margin_frac: float = 0.08
    # [pattern, replacement] pairs (case-insensitive) applied to every output
    # string - identifiers, titles, attribute values, body text, figure
    # captions - after extraction. A general-purpose find/replace over the
    # extracted corpus; the tool has no opinion on what it's used for.
    redact: list = dataclasses.field(default_factory=list)
    # Escape hatch for figures/tables the heuristic in _crop_figure couldn't
    # bound automatically (see ExtractionReport.figures_skipped for candidates
    # - it reports the node each one was nearest to and its source page).
    # Each entry: {"node_id": <pre-redaction identifier from the report>,
    #   "source_page": <1-based physical page>, "slice_index": <0-based, default 0>,
    #   "bbox": [x0, y0, x1, y1] in that logical page's local point space, or
    #   omitted/null to crop the whole logical page, "caption": <optional>}.
    manual_figures: list = dataclasses.field(default_factory=list)
    # --polish only: approximate token budget per batched LLM request (see
    # polish.py's DEFAULT_POLISH_BATCH_TOKENS and the "lost in the middle"
    # discussion in its apply_polish docstring). None -> use that default.
    # --polish-batch-tokens on the CLI overrides this when both are given.
    polish_batch_tokens: int | None = None
    # --polish only: "strict" (default) screens every body/reply for
    # prompt-injection phrasing before sending; "off" disables both
    # screens - for a trusted document that legitimately shares this
    # vocabulary (one that itself specifies an AI/LLM system's behavior).
    # See apply_polish's docstring in polish.py. --polish-injection-check
    # on the CLI overrides this when both are given.
    polish_injection_check: str = "strict"


@dataclasses.dataclass
class _Line:
    page_key: tuple
    y: float
    x: float
    text: str
    logical_page: LogicalPage


def _merge_rows(page: LogicalPage, tolerance: float) -> list[_Line]:
    """Group text blocks that sit on the same visual row into one combined line."""
    rows: list[list] = []
    for block in page.blocks:
        y0 = block.bbox[1]
        placed = False
        for row in rows:
            if abs(row[0] - y0) <= tolerance:
                row[1].append(block)
                placed = True
                break
        if not placed:
            rows.append([y0, [block]])
    rows.sort(key=lambda r: r[0])
    lines = []
    for y0, blocks in rows:
        blocks.sort(key=lambda b: b.bbox[0])
        text = "    ".join(b.text.replace("\n", " ").strip() for b in blocks)
        x0 = blocks[0].bbox[0]
        lines.append(_Line(
            page_key=(page.source_page_index, page.slice_index),
            y=y0, x=x0, text=text, logical_page=page,
        ))
    return lines


_DIGITS_RE = re.compile(r"\d+")


def _boilerplate_template(text: str) -> str:
    """Collapse digit runs so a per-page number (e.g. a page count) doesn't
    defeat repetition detection for an otherwise-identical header/footer row."""
    return _DIGITS_RE.sub("#", text)


_BOILERPLATE_FRAGMENT_MIN_LEN = 6
_BOILERPLATE_EXTENDED_MARGIN_MULTIPLIER = 2.0
_BOILERPLATE_EXTENDED_MARGIN_CAP = 0.4


def _strip_boilerplate(lines: list[_Line], pages: list[LogicalPage], cfg: Config, report: ExtractionReport) -> list[_Line]:
    """Drop running headers/footers: text repeating (up to embedded page numbers) in the same margin band.

    Two passes. First, exact per-line repetition within the configured
    margin bands, same as a simple running-header/footer detector. Second,
    a wider margin band catches the same boilerplate where a print layout's
    label/value pairs happened to land as separate rows on some pages but
    merged into one on others (e.g. a slightly larger vertical gap) - any
    line there whose normalized text is a substantial *substring* of an
    already-confirmed boilerplate line is boilerplate too, without needing
    to independently clear the repetition threshold on its own (a lone
    fragment naturally recurs less often than the merged whole).
    """
    page_heights = {(p.source_page_index, p.slice_index): p.height for p in pages}
    n_pages = max(1, len(pages))
    threshold = max(3, n_pages * cfg.boilerplate_min_page_frac)

    def in_margin(line: _Line, header_frac: float, footer_frac: float) -> bool:
        h = page_heights[line.page_key]
        return line.y <= h * header_frac or line.y >= h * (1 - footer_frac)

    margin_templates = Counter(
        _boilerplate_template(line.text)
        for line in lines
        if in_margin(line, cfg.header_margin_frac, cfg.footer_margin_frac)
    )
    confirmed = {template for template, count in margin_templates.items() if count >= threshold}

    ext_header = min(cfg.header_margin_frac * _BOILERPLATE_EXTENDED_MARGIN_MULTIPLIER, _BOILERPLATE_EXTENDED_MARGIN_CAP)
    ext_footer = min(cfg.footer_margin_frac * _BOILERPLATE_EXTENDED_MARGIN_MULTIPLIER, _BOILERPLATE_EXTENDED_MARGIN_CAP)

    def is_boilerplate(line: _Line) -> bool:
        template = _boilerplate_template(line.text)
        if template in confirmed:
            return True
        if len(template) < _BOILERPLATE_FRAGMENT_MIN_LEN:
            return False
        if not in_margin(line, ext_header, ext_footer):
            return False
        return any(template in whole for whole in confirmed)

    kept = [line for line in lines if not is_boilerplate(line)]
    report.boilerplate_lines_dropped += len(lines) - len(kept)
    return kept


def _in_figure_window(bbox, cfg: Config, prev_boundary_y: float, caption_y: float,
                       next_boundary_y: float | None) -> bool:
    if cfg.figure_caption_position == "below":
        return bbox[1] >= prev_boundary_y and bbox[3] <= caption_y + cfg.figure_caption_gap
    # "above": the graphic follows its caption, so the window is bounded below by
    # the caption line and above by whatever structural element comes next (the
    # next id/heading/figure match, or the page edge if none) - symmetric with the
    # "below" case's own upper bound, so a caption can't sweep in unrelated content
    # for the rest of the page.
    upper = next_boundary_y if next_boundary_y is not None else float("inf")
    return bbox[1] >= caption_y - cfg.figure_caption_gap and bbox[3] <= upper


def _next_boundary_y(all_lines: list[_Line], start_idx: int, page_key: tuple,
                      id_re: re.Pattern, heading_re: re.Pattern | None, figure_re: re.Pattern) -> float | None:
    """The y of the next id/heading/figure match on the same page, for bounding
    a `figure_caption_position: "above"` search - or None if the page ends first."""
    for later in all_lines[start_idx + 1:]:
        if later.page_key != page_key:
            return None
        if id_re.match(later.text) or (heading_re and heading_re.match(later.text)) or figure_re.match(later.text):
            return later.y
    return None


def _crop_figure(page: LogicalPage, caption_line: _Line, cfg: Config, prev_boundary_y: float,
                  next_boundary_y: float | None) -> bytes | None:
    caption_y = caption_line.y
    candidates = [
        bx for bx in page.vector_bboxes
        if _in_figure_window(bx, cfg, prev_boundary_y, caption_y, next_boundary_y)
    ]
    candidates += [
        img.bbox for img in page.images
        if _in_figure_window(img.bbox, cfg, prev_boundary_y, caption_y, next_boundary_y)
    ]
    region = None
    for bx in candidates:
        region = bx if region is None else (
            min(region[0], bx[0]), min(region[1], bx[1]),
            max(region[2], bx[2]), max(region[3], bx[3]),
        )
    if region is None:
        return None
    return page.render_region(region, dpi=cfg.figure_dpi, padding=cfg.figure_padding)


def extract(pages: list[LogicalPage], cfg: Config) -> tuple[list[Node], ExtractionReport]:
    from .node_builder import NodeBuilder

    report = ExtractionReport(logical_pages=len(pages))
    figure_re = re.compile(cfg.figure_pattern)
    builder = NodeBuilder(cfg, report, join_paragraphs=False)

    all_lines: list[_Line] = []
    for page in pages:
        all_lines.extend(_merge_rows(page, cfg.row_merge_tolerance))
    all_lines = _strip_boilerplate(all_lines, pages, cfg, report)

    prev_boundary_y = 0.0
    prev_page_key = None

    for idx, line in enumerate(all_lines):
        if line.page_key != prev_page_key:
            # Start each page's figure-region search below the running header,
            # not at y=0 - otherwise a caption appearing early on the page can
            # accidentally sweep in the header's own border/rule graphics.
            prev_boundary_y = line.logical_page.height * cfg.figure_top_margin_frac
            prev_page_key = line.page_key

        id_match = builder.match_id(line.text)
        heading_match = builder.match_heading(line.text)
        figure_match = figure_re.match(line.text)
        label_match = builder.match_label(line.text, blocked=bool(id_match or heading_match or figure_match))

        if id_match:
            groups = id_match.groupdict()
            title = groups.get("title")
            if title is None:
                title = line.text[id_match.end():].strip(" \t-:")
            builder.attach_requirement(groups["id"], title)
            prev_boundary_y = line.y
            continue

        if heading_match:
            number = heading_match.group("number")
            builder.attach_heading(number, number.count(".") + 1, heading_match.group("title"))
            prev_boundary_y = line.y
            continue

        if figure_match:
            next_boundary_y = (
                _next_boundary_y(all_lines, idx, line.page_key, builder.id_re, builder.heading_re, figure_re)
                if cfg.figure_caption_position == "above" else None
            )
            png = _crop_figure(line.logical_page, line, cfg, prev_boundary_y, next_boundary_y)
            builder.attach_figure(
                png, line.text.strip(),
                source_page=line.logical_page.source_page_index + 1,
                slice_index=line.logical_page.slice_index,
            )
            prev_boundary_y = line.y
            continue

        if label_match:
            builder.record_label(label_match.group("label").strip(), label_match.group("value").strip())
            continue

        builder.append_body(line.text)

    builder.finish()
    return builder.roots, report


def apply_redactions(roots: list[Node], rules: list) -> None:
    """Rewrite every output string on the tree in place per (pattern, replacement) rules."""
    if not rules:
        return
    compiled = [(re.compile(pattern, re.IGNORECASE), replacement) for pattern, replacement in rules]

    def clean(text: str) -> str:
        for pattern, replacement in compiled:
            text = pattern.sub(replacement, text)
        return text

    def walk(nodes: list[Node]) -> None:
        for node in nodes:
            node.identifier = clean(node.identifier)
            node.title = clean(node.title)
            node.attributes = {k: clean(v) for k, v in node.attributes.items()}
            node.body_lines = [clean(line) for line in node.body_lines]
            for fig in node.figures:
                fig.caption = clean(fig.caption)
            walk(node.children)

    walk(roots)


def apply_manual_figures(roots: list[Node], pages: list[LogicalPage], entries: list) -> list:
    """Attach hand-specified figure crops the automatic heuristic couldn't bound.

    Returns the list of entries that couldn't be applied (unknown node_id or
    out-of-range page/slice), so the caller can surface them rather than
    silently drop a correction.
    """
    if not entries:
        return []

    node_index: dict[str, Node] = {}

    def index(nodes: list[Node]) -> None:
        for node in nodes:
            node_index.setdefault(node.identifier, node)
            index(node.children)

    index(roots)
    page_index = {(p.source_page_index, p.slice_index): p for p in pages}

    failed = []
    replaced: set[str] = set()
    for i, entry in enumerate(entries):
        node = node_index.get(entry.get("node_id"))
        page = page_index.get((entry.get("source_page", 0) - 1, entry.get("slice_index", 0)))
        if node is None or page is None:
            failed.append(entry)
            continue
        # Default to replacing whatever the heuristic already attached to this
        # node - a manual entry exists because that result was wrong, not to
        # supplement it. Only the first entry per node clears the list, so
        # multiple manual figures for the same node still all land.
        if entry.get("replace", True) and node.identifier not in replaced:
            node.figures.clear()
            replaced.add(node.identifier)
        bbox = entry.get("bbox") or (0, 0, page.width, page.height)
        png = page.render_region(tuple(bbox), dpi=entry.get("dpi", 200), padding=entry.get("padding", 0.0))
        node.figures.append(Figure(
            caption=entry.get("caption", ""),
            image_png=png,
            order=1000 + i,
        ))
    return failed
