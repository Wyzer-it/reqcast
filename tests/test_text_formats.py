from reqcast.text_formats import extract_txt, extract_markdown


def test_txt_basic_extraction(base_cfg):
    text = (
        "1 Overview\n"
        "REQ-001 First\n"
        "Description: The system shall do the first thing.\n"
        "Rationale: Because.\n"
    )
    roots, report = extract_txt(text, base_cfg)
    assert report.headings == 1
    assert report.requirements == 1
    req = roots[0].children[0]
    assert req.identifier == "REQ-001"
    assert req.title == "First"
    # Rationale arrives after body_label ("Description") opened, so it's
    # folded into the body as prose rather than becoming its own attribute -
    # same documented semantics as the PDF path.
    assert "Because." in req.body
    assert "Rationale" in req.body


def test_txt_joins_wrapped_lines_into_one_paragraph(base_cfg):
    text = (
        "REQ-001 Wrapping\n"
        "Description: This sentence continues\n"
        "across two lines without a blank line.\n"
        "\n"
        "This is a second paragraph.\n"
    )
    roots, _ = extract_txt(text, base_cfg)
    body_lines = roots[0].body_lines
    assert len(body_lines) == 2
    assert body_lines[0] == "This sentence continues across two lines without a blank line."
    assert body_lines[1] == "This is a second paragraph."


def test_txt_figure_caption_is_found_but_not_croppable(base_cfg):
    text = "REQ-001 X\nDescription: text.\n\nFigure 1: A diagram\n"
    roots, report = extract_txt(text, base_cfg)
    assert report.figures_found == 1
    assert report.figures_cropped == 0
    assert len(report.figures_skipped) == 1
    assert roots[0].figures == []


def test_markdown_native_headings_and_id(tmp_path, base_cfg):
    text = "# Title\n\n## 1.1 Section\n\n- REQ-001 First\nDescription: text.\n"
    roots, report = extract_markdown(text, tmp_path, base_cfg)
    assert report.headings == 2
    assert report.requirements == 1
    # depth-1 "# Title" then depth-2 "## Section" nested under it
    assert roots[0].kind == "heading"
    assert roots[0].children[0].kind == "heading"
    req = roots[0].children[0].children[0]
    assert req.identifier == "REQ-001"
    assert req.title == "First"


def test_markdown_inline_image_attaches_with_alt_as_caption(tmp_path, base_cfg):
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000100ffff03000006"
        "0005579f74140000000049454e44ae426082"
    )
    (tmp_path / "diagram.png").write_bytes(png_bytes)
    text = "REQ-001 X\nDescription: text.\n\n![System diagram](diagram.png)\n"
    roots, report = extract_markdown(text, tmp_path, base_cfg)
    assert report.figures_found == 1
    assert report.figures_cropped == 1
    assert roots[0].figures[0].caption == "System diagram"
    assert roots[0].figures[0].image_png == png_bytes


def test_markdown_missing_image_file_is_reported_not_swallowed_into_body(tmp_path, base_cfg):
    """Regression: a Markdown image whose file can't be loaded used to fall
    through every branch and get appended raw ('![alt](path)') to the
    requirement's body text, with the failure invisible in the report."""
    text = "REQ-001 X\nDescription: text.\n\n![Missing](does-not-exist.png)\nMore text after.\n"
    roots, report = extract_markdown(text, tmp_path, base_cfg)
    assert report.figures_found == 1
    assert report.figures_cropped == 0
    assert len(report.figures_skipped) == 1
    assert roots[0].figures == []
    body = roots[0].body
    assert "![Missing]" not in body
    assert "does-not-exist.png" not in body
    assert "More text after." in body
