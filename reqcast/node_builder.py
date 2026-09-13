"""Shared id/heading/label/body/figure bookkeeping used by every format's
extractor (extract.py for PDF, text_formats.py for txt/Markdown,
docx_extract.py for .docx).

Only the part that's identical across formats lives here: matching against
id_pattern/heading_pattern/label_pattern, building the node tree with
heading-nesting via a depth stack, accumulating a requirement's body text,
and attaching a figure (with the found/cropped/skipped counters) to
whichever node is "current" - including the found-but-not-attachable case
that PDF's own copy of this logic used to miscount as cropped (see
CHANGELOG / the 2026-09-13 review). Reading the source (PDF page geometry,
python-docx paragraphs, plain lines) and locating candidate figure images
stays in each format's own module - those genuinely differ per format and
gain nothing from being forced through one code path.

`join_paragraphs` controls how body text accumulates: PDF passes False to
keep its existing, documented "one PDF line -> one paragraph" behavior
unchanged (needed because a PDF's own line-wrap isn't the same thing as a
real paragraph break, which is exactly what --polish exists to repair
afterwards). Markdown/.txt/.docx pass True, since consecutive non-blank
lines in those formats already are one real paragraph - joining them here
is what lets those formats skip needing --polish at all.
"""
from __future__ import annotations

import re

from .extract import Config, ExtractionReport, Figure, Node


class NodeBuilder:
    def __init__(self, cfg: Config, report: ExtractionReport, *, join_paragraphs: bool) -> None:
        self.cfg = cfg
        self.report = report
        self.join_paragraphs = join_paragraphs
        self.id_re = re.compile(cfg.id_pattern)
        self.heading_re = re.compile(cfg.heading_pattern) if cfg.heading_pattern else None
        self.label_re = re.compile(cfg.label_pattern)
        self.roots: list[Node] = []
        self.stack: list[Node] = []
        self.current: Node | None = None
        self.body_open = False
        self.order = 0
        self._figure_order = 0
        self._para_buffer: list[str] = []

    # -- classification ------------------------------------------------
    def match_id(self, text: str):
        return self.id_re.match(text)

    def match_heading(self, text: str):
        return self.heading_re.match(text) if self.heading_re else None

    def match_label(self, text: str, *, blocked: bool):
        if blocked or self.body_open:
            return None
        return self.label_re.match(text)

    # -- body text -------------------------------------------------------
    def flush_para(self) -> None:
        if self.join_paragraphs and self._para_buffer and self.current is not None and self.body_open:
            self.current.body_lines.append(" ".join(self._para_buffer))
        self._para_buffer.clear()

    def append_body(self, text: str) -> None:
        if self.current is None or not self.body_open:
            return
        if self.join_paragraphs:
            self._para_buffer.append(text)
        else:
            self.current.body_lines.append(text)

    # -- tree building -----------------------------------------------------
    def _attach(self, node: Node) -> None:
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)
        self.current = node
        self.body_open = self.cfg.body_label is None

    def attach_requirement(self, identifier: str, title: str) -> Node:
        self.flush_para()
        self.order += 1
        node = Node("requirement", identifier, title.strip(), {}, [], [], [], self.order)
        self._attach(node)
        self.report.requirements += 1
        return node

    def attach_heading(self, number: str, depth: int, title: str) -> Node:
        self.flush_para()
        self.order += 1
        while len(self.stack) >= depth:
            self.stack.pop()
        node = Node("heading", number, title.strip(), {}, [], [], [], self.order)
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)
        self.stack.append(node)
        self.current = node
        self.body_open = self.cfg.body_label is None
        self.report.headings += 1
        return node

    def record_label(self, label: str, value: str) -> None:
        self.flush_para()
        self.report.discovered_attribute_labels[label] += 1
        if self.current is not None:
            self.current.attributes[label] = value
            if self.cfg.body_label is not None and label.lower() == self.cfg.body_label.lower():
                self.body_open = True
                if value:
                    self.append_body(value)

    # -- figures -------------------------------------------------------------
    def attach_figure(self, png: bytes | None, caption: str, *, source_page: int = 1, slice_index: int = 0) -> None:
        """`png=None` covers every "found but couldn't actually attach" case
        uniformly: no bounding box found (PDF), a paragraph with no target
        node yet (any format), or an image file that failed to load/convert
        (Markdown) - all correctly land in figures_skipped, never miscounted
        as cropped."""
        self.flush_para()
        self.report.figures_found += 1
        self._figure_order += 1
        target = self.current if self.current is not None else (self.stack[-1] if self.stack else None)
        if png is not None and target is not None:
            self.report.figures_cropped += 1
            target.figures.append(Figure(caption=caption, image_png=png, order=self._figure_order))
        else:
            self.report.figures_skipped.append({
                "caption": caption, "source_page": source_page, "slice_index": slice_index,
                "near_node": target.identifier if target else None,
            })

    def flush_orphan_figure(self, caption: str | None) -> None:
        """For a format (.docx) that buffers an image/caption waiting for its
        pair: report one that never got resolved before a section boundary,
        instead of letting it leak into the next node."""
        self.flush_para()
        self.report.figures_found += 1
        target = self.current if self.current is not None else (self.stack[-1] if self.stack else None)
        self.report.figures_skipped.append({
            "caption": caption or "", "source_page": 1, "slice_index": 0,
            "near_node": target.identifier if target else None,
        })

    def finish(self) -> None:
        self.flush_para()
