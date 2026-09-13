import pymupdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from reqcast.extract import Config, _in_figure_window, extract
from reqcast.layout import iter_logical_pages


def _open(path):
    doc = pymupdf.open(str(path))
    return iter_logical_pages(doc)


def test_pdf_basic_extraction(tmp_path, base_cfg):
    path = tmp_path / "spec.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "REQ-001 First requirement")
    c.drawString(72, 680, "Description: The system shall do the first thing.")
    c.save()

    roots, report = extract(_open(path), base_cfg)
    assert report.requirements == 1
    assert roots[0].identifier == "REQ-001"
    assert "shall do the first thing" in roots[0].body


def test_pdf_figure_before_any_node_is_skipped_not_miscounted(tmp_path, base_cfg):
    """Regression: a figure caption appearing before the first heading or
    requirement (e.g. a cover-page diagram) has no node to attach to. The
    old code still incremented figures_cropped unconditionally once the
    crop succeeded, so the summary claimed a successful crop for an image
    that was actually dropped with no trace in figures_skipped."""
    path = tmp_path / "spec.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    # A real vector shape for _crop_figure to find, with no id/heading above it.
    c.rect(72, 650, 100, 40, stroke=1, fill=0)
    c.drawString(72, 630, "Figure 1: Cover diagram")
    c.drawString(72, 600, "REQ-001 First requirement")
    c.drawString(72, 580, "Description: text.")
    c.save()

    roots, report = extract(_open(path), base_cfg)
    assert report.figures_found == 1
    assert report.figures_cropped == 0, "a figure with no attachable node must not count as cropped"
    assert len(report.figures_skipped) == 1
    assert report.figures_skipped[0]["near_node"] is None
    # and it must genuinely not be attached anywhere
    assert all(not n.figures for n in roots)


def test_above_caption_position_window_is_bounded_by_next_structural_line():
    """Regression: with figure_caption_position="above", `_in_figure_window`
    had no upper y-bound at all - `bbox[1] >= caption_y - gap` with nothing
    capping the far side, unlike the symmetric "below" branch. A shape
    belonging to a later, unrelated section (past the next id/heading/figure
    match) used to be swept into the same crop as the real one. Bounding by
    `next_boundary_y` (the y of that next match, computed by
    `_next_boundary_y` via lookahead) fixes it without touching the "below"
    branch's own already-correct bounds."""
    cfg = Config(id_pattern=r"^(?P<id>REQ-\d{3})(?:\s+(?P<title>\S.*))?$",
                 figure_caption_position="above")
    caption_y = 100.0
    next_boundary_y = 200.0

    # Belongs to this caption: starts right after it, well before the next
    # structural line.
    assert _in_figure_window((10, 110, 50, 150), cfg, 0.0, caption_y, next_boundary_y)

    # Belongs to the NEXT section (starts past next_boundary_y) - must be
    # excluded now that the window is bounded.
    assert not _in_figure_window((10, 210, 50, 250), cfg, 0.0, caption_y, next_boundary_y)

    # Sanity: the same later shape was NOT excluded under the old unbounded
    # behavior (next_boundary_y=None), proving the bound is what changed.
    assert _in_figure_window((10, 210, 50, 250), cfg, 0.0, caption_y, None)


def test_pdf_above_caption_position_end_to_end(tmp_path):
    """Integration-level companion to the unit test above: a real PDF where
    the figure immediately follows its "above" caption, with a second,
    unrelated shape after the next requirement. Only the first shape may be
    cropped and attached."""
    cfg = Config(id_pattern=r"^(?P<id>REQ-\d{3})(?:\s+(?P<title>\S.*))?$",
                 figure_caption_position="above")
    path = tmp_path / "spec.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("Helvetica", 12)
    c.drawString(72, 700, "REQ-001 First")
    c.drawString(72, 680, "Figure 1: Right below this caption")
    c.rect(72, 640, 80, 20, stroke=1, fill=0)  # the actual figure for REQ-001
    c.drawString(72, 560, "REQ-002 Second")  # next structural boundary
    c.rect(72, 400, 200, 100, stroke=1, fill=0)  # unrelated shape, well past REQ-002
    c.save()

    roots, report = extract(_open(path), cfg)
    assert report.requirements == 2
    assert report.figures_cropped == 1
    assert len(roots[0].figures) == 1
    assert roots[1].figures == []
