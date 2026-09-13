import pymupdf
from reportlab.pdfgen import canvas

from reqcast.layout import iter_logical_pages


def test_slice_boundary_straddling_text_is_not_dropped(tmp_path):
    """Regression: a text block sitting almost exactly on a slices_per_sheet
    boundary used to be silently dropped from BOTH slices when neither side
    alone reached the old 60%-area-inside threshold (e.g. a ~47/53 split
    clears neither). It must now land in whichever slice has the larger
    share, never disappear."""
    path = tmp_path / "straddle.pdf"
    c = canvas.Canvas(str(path), pagesize=(800, 400))
    c.setFont("Helvetica", 14)
    c.drawString(60, 350, "REQ-001 Left side")
    c.drawString(460, 350, "REQ-002 Right side")
    # Straddles x=400, the slices_per_sheet=2 horizontal boundary, with a
    # slightly larger share on the right (roughly 30pt left / 34pt right).
    c.drawString(370, 250, "STRADDLE")
    c.save()

    doc = pymupdf.open(str(path))
    pages = iter_logical_pages(doc, slices_per_sheet=2, slice_direction="horizontal")
    assert len(pages) == 2

    all_text = " ".join(b.text for p in pages for b in p.blocks)
    assert "STRADDLE" in all_text, "straddling content must land in exactly one slice, not vanish from both"

    # It should land in exactly one slice, not both (no duplication either).
    slices_containing_it = [p.slice_index for p in pages if any("STRADDLE" in b.text for b in p.blocks)]
    assert len(slices_containing_it) == 1


def test_non_straddling_content_still_assigns_correctly(tmp_path):
    """Sanity check that the fix didn't break the ordinary, non-straddling
    case: content clearly inside one slice must not leak into the other."""
    path = tmp_path / "clean-split.pdf"
    c = canvas.Canvas(str(path), pagesize=(800, 400))
    c.setFont("Helvetica", 14)
    # Different rows (not just different x on the same row) so PyMuPDF's own
    # text-block grouping can't merge them into a single wide block regardless
    # of the slicing logic under test.
    c.drawString(60, 350, "LEFT-ONLY")
    c.drawString(460, 300, "RIGHT-ONLY")
    c.save()

    doc = pymupdf.open(str(path))
    pages = iter_logical_pages(doc, slices_per_sheet=2, slice_direction="horizontal")
    left_text = " ".join(b.text for b in pages[0].blocks)
    right_text = " ".join(b.text for b in pages[1].blocks)
    assert "LEFT-ONLY" in left_text and "RIGHT-ONLY" not in left_text
    assert "RIGHT-ONLY" in right_text and "LEFT-ONLY" not in right_text
