import base64
import zipfile
from xml.etree import ElementTree as ET

from reqcast.extract import Figure, Node
from reqcast.reqif import ReqIfWriter

REQIF_NS = "{http://www.omg.org/spec/ReqIF/20110401/reqif.xsd}"


def _req(identifier, title, attrs, body):
    return Node("requirement", identifier, title, attrs, [body] if body else [], [], [], 1)


def test_distinct_labels_get_distinct_attribute_ids():
    """Regression: two distinct discovered labels that sanitize to the same
    identifier (any non-alphanumeric character becomes '_') used to collide,
    producing two ATTRIBUTE-DEFINITION-STRING elements with the same
    IDENTIFIER - invalid/ambiguous ReqIF."""
    n1 = _req("REQ-001", "First", {"Owner-Team": "Alpha"}, "text one")
    n2 = _req("REQ-002", "Second", {"Owner Team": "Beta"}, "text two")

    writer = ReqIfWriter(title="Test")
    xml = writer.render([n1, n2])

    root = ET.fromstring(xml)
    ids = [
        el.get("IDENTIFIER")
        for el in root.iter(f"{REQIF_NS}ATTRIBUTE-DEFINITION-STRING")
    ]
    assert len(ids) == len(set(ids)), f"duplicate attribute-definition IDENTIFIERs: {ids}"

    owner_ids = {i for i in ids if "Owner" in i}
    assert len(owner_ids) == 2, "Owner-Team and Owner Team must resolve to two distinct definitions"


def test_render_produces_well_formed_xml_with_figure():
    node = _req("REQ-001", "First", {}, "text with a figure")
    node.figures.append(Figure(caption="A diagram", image_png=b"\x89PNG\r\n\x1a\n", order=1))
    writer = ReqIfWriter(title="Test")
    xml = writer.render([node])
    ET.fromstring(xml)  # raises if malformed


def test_embed_images_produces_base64_data_uri_and_no_media_assets():
    node = _req("REQ-001", "First", {}, "text")
    node.figures.append(Figure(caption="cap", image_png=b"\x89PNG\r\n\x1a\n", order=1))

    writer = ReqIfWriter(title="Test", embed_images=True)
    xml = writer.render([node])
    assert "data:image/png;base64," in xml
    assert base64.b64encode(b"\x89PNG\r\n\x1a\n").decode() in xml
    assert not writer._assets


def test_package_vs_write_reqif(tmp_path):
    node = _req("REQ-001", "First", {}, "text")
    node.figures.append(Figure(caption="cap", image_png=b"\x89PNG\r\n\x1a\n", order=1))

    writer = ReqIfWriter(title="Test")
    xml = writer.render([node])

    zip_path = tmp_path / "out.reqifz"
    writer.package(xml, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        assert "out.reqif" in names
        assert any(n.startswith("media/") for n in names)

    plain_path = tmp_path / "out.reqif"
    writer2 = ReqIfWriter(title="Test")
    xml2 = writer2.render([node])
    writer2.write_reqif(xml2, plain_path)
    assert plain_path.exists()
    assert (tmp_path / "media").exists()
    assert list((tmp_path / "media").iterdir())
