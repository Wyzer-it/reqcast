"""Normalizes a PDF's pages into an ordered stream of "logical pages".

Handles two layout wrinkles seen in exported requirements documents:

- a page rotation (e.g. a landscape export of otherwise-portrait pages)
- N-up "booklet" sheets, where one physical PDF page holds several
  independent logical document pages side by side (or stacked)

Both are optional and off by default (rotation is read from the PDF itself;
``slices_per_sheet=1`` is a no-op split), so a plain single-column PDF
normalizes to one logical page per physical page, unchanged.
"""
from __future__ import annotations

import dataclasses

import pymupdf


@dataclasses.dataclass
class TextBlock:
    text: str
    # (x0, y0, x1, y1) in this logical page's own local coordinate space
    bbox: tuple[float, float, float, float]


@dataclasses.dataclass
class RasterImage:
    bbox: tuple[float, float, float, float]
    data: bytes
    ext: str


@dataclasses.dataclass
class LogicalPage:
    source_page_index: int
    slice_index: int
    width: float
    height: float
    blocks: list[TextBlock]
    vector_bboxes: list[tuple[float, float, float, float]]
    images: list[RasterImage]
    _page: pymupdf.Page
    _origin: tuple[float, float]  # this slice's offset within the rendered/rotated sheet

    def render_region(self, local_bbox, dpi: int = 200, padding: float = 6.0) -> bytes:
        """Render a region of this logical page (in its local coords) to PNG bytes."""
        x0, y0, x1, y1 = local_bbox
        x0, y0 = x0 - padding, y0 - padding
        x1, y1 = x1 + padding, y1 + padding
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(self.width, x1), min(self.height, y1)
        ox, oy = self._origin
        clip = pymupdf.Rect(x0 + ox, y0 + oy, x1 + ox, y1 + oy)
        zoom = dpi / 72.0
        pix = self._page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        return pix.tobytes("png")


def _slice_bounds(total: float, slices: int, index: int) -> tuple[float, float]:
    step = total / slices
    return step * index, step * (index + 1)


def _best_slice_index(rect: pymupdf.Rect, slice_rects: list[pymupdf.Rect]) -> int | None:
    """Assign to whichever slice this rect overlaps the most. A block sitting
    exactly on a `slices_per_sheet` boundary splits its area between the two
    adjacent slices; picking the larger share (rather than requiring one side
    alone to clear a fixed threshold) guarantees it lands in exactly one
    slice instead of being silently dropped from both when neither share
    happens to clear that threshold. Returns None only for a rect with no
    overlap anywhere (zero area, or genuinely outside every slice)."""
    best_idx, best_overlap = None, 0.0
    for i, slice_rect in enumerate(slice_rects):
        overlap = (rect & slice_rect).get_area()
        if overlap > best_overlap:
            best_idx, best_overlap = i, overlap
    return best_idx


def iter_logical_pages(
    doc: pymupdf.Document,
    slices_per_sheet: int = 1,
    slice_direction: str = "horizontal",
) -> list[LogicalPage]:
    """Flatten every physical page into 1+ reading-order logical pages.

    ``slices_per_sheet`` > 1 splits each physical page (after its own
    ``/Rotate`` is applied) into that many equal slices along
    ``slice_direction`` ("horizontal" or "vertical"), in reading order. This
    models a booklet-style export where several independent document pages
    share one rotated sheet.
    """
    logical_pages: list[LogicalPage] = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        rot_matrix = page.rotation_matrix
        page_rect = page.rect  # already in rotated/display space

        raw_blocks = page.get_text("blocks")
        raw_drawings = page.get_drawings()
        raw_images = _extract_raster_images(doc, page)

        slice_rects: list[pymupdf.Rect] = []
        for slice_index in range(slices_per_sheet):
            if slice_direction == "horizontal":
                lo, hi = _slice_bounds(page_rect.width, slices_per_sheet, slice_index)
                slice_rects.append(pymupdf.Rect(page_rect.x0 + lo, page_rect.y0,
                                                 page_rect.x0 + hi, page_rect.y1))
            else:
                lo, hi = _slice_bounds(page_rect.height, slices_per_sheet, slice_index)
                slice_rects.append(pymupdf.Rect(page_rect.x0, page_rect.y0 + lo,
                                                 page_rect.x1, page_rect.y0 + hi))
        origins = [(sr.x0, sr.y0) for sr in slice_rects]

        blocks_per_slice = _classify_blocks(raw_blocks, rot_matrix, slice_rects, origins)
        vectors_per_slice = _classify_vector_bboxes(raw_drawings, rot_matrix, slice_rects, origins)
        images_per_slice = _classify_images(raw_images, rot_matrix, slice_rects, origins)

        for slice_index, slice_rect in enumerate(slice_rects):
            logical_pages.append(LogicalPage(
                source_page_index=page_index,
                slice_index=slice_index,
                width=slice_rect.width,
                height=slice_rect.height,
                blocks=blocks_per_slice[slice_index],
                vector_bboxes=vectors_per_slice[slice_index],
                images=images_per_slice[slice_index],
                _page=page,
                _origin=origins[slice_index],
            ))
    return logical_pages


def _classify_blocks(raw_blocks, rot_matrix, slice_rects, origins) -> list[list[TextBlock]]:
    out: list[list[TextBlock]] = [[] for _ in slice_rects]
    for b in raw_blocks:
        text = b[4].strip()
        if not text:
            continue
        rotated = pymupdf.Rect(b[0], b[1], b[2], b[3]) * rot_matrix
        idx = _best_slice_index(rotated, slice_rects)
        if idx is None:
            continue
        ox, oy = origins[idx]
        out[idx].append(TextBlock(
            text=text,
            bbox=(rotated.x0 - ox, rotated.y0 - oy, rotated.x1 - ox, rotated.y1 - oy),
        ))
    for blocks in out:
        blocks.sort(key=lambda blk: (round(blk.bbox[1], 1), blk.bbox[0]))
    return out


def _classify_vector_bboxes(raw_drawings, rot_matrix, slice_rects, origins) -> list[list[tuple]]:
    out: list[list[tuple]] = [[] for _ in slice_rects]
    for d in raw_drawings:
        rotated = d["rect"] * rot_matrix
        idx = _best_slice_index(rotated, slice_rects)
        if idx is None:
            continue
        ox, oy = origins[idx]
        out[idx].append((rotated.x0 - ox, rotated.y0 - oy, rotated.x1 - ox, rotated.y1 - oy))
    return out


def _classify_images(raw_images, rot_matrix, slice_rects, origins) -> list[list[RasterImage]]:
    out: list[list[RasterImage]] = [[] for _ in slice_rects]
    for img in raw_images:
        rotated = img.bbox_rect * rot_matrix
        idx = _best_slice_index(rotated, slice_rects)
        if idx is None:
            continue
        ox, oy = origins[idx]
        out[idx].append(RasterImage(
            bbox=(rotated.x0 - ox, rotated.y0 - oy, rotated.x1 - ox, rotated.y1 - oy),
            data=img.data,
            ext=img.ext,
        ))
    return out


@dataclasses.dataclass
class _RawImage:
    bbox_rect: pymupdf.Rect
    data: bytes
    ext: str


def _extract_raster_images(doc: pymupdf.Document, page: pymupdf.Page) -> list[_RawImage]:
    out = []
    seen_xrefs = set()
    for img in page.get_image_info(xrefs=True):
        xref = img.get("xref", 0)
        if not xref or xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)
        try:
            base = doc.extract_image(xref)
        except Exception:
            continue
        out.append(_RawImage(bbox_rect=pymupdf.Rect(img["bbox"]), data=base["image"], ext=base["ext"]))
    return out
