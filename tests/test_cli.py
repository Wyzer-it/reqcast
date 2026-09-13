import json
import zipfile

import pytest

from reqcast.cli import main


def _write_config(tmp_path, **overrides):
    cfg = {"id_pattern": r"^(?P<id>REQ-\d{3})(?:\s+(?P<title>\S.*))?$", "body_label": "Description"}
    cfg.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    return path


def test_dispatches_txt_by_extension(tmp_path, capsys):
    src = tmp_path / "spec.txt"
    src.write_text("REQ-001 First\nDescription: text.\n")
    out = tmp_path / "out.reqifz"
    cfg = _write_config(tmp_path)

    rc = main([str(src), str(out), "--config", str(cfg)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["source_format"] == "txt"
    assert summary["requirements"] == 1
    assert out.exists()


def test_dispatches_markdown_by_extension(tmp_path, capsys):
    src = tmp_path / "spec.md"
    src.write_text("# Title\n\nREQ-001 First\nDescription: text.\n")
    out = tmp_path / "out.reqifz"
    cfg = _write_config(tmp_path)

    rc = main([str(src), str(out), "--config", str(cfg)])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["source_format"] == "md"


def test_rejects_unsupported_extension(tmp_path):
    src = tmp_path / "spec.rtf"
    src.write_text("whatever")
    with pytest.raises(SystemExit):
        main([str(src), str(tmp_path / "out.reqifz"), "--config", str(_write_config(tmp_path))])


def test_rejects_pdf_only_flags_for_non_pdf(tmp_path):
    src = tmp_path / "spec.txt"
    src.write_text("REQ-001 First\nDescription: text.\n")
    cfg = _write_config(tmp_path)
    with pytest.raises(SystemExit, match="only apply to PDF"):
        main([str(src), str(tmp_path / "out.reqifz"), "--config", str(cfg), "--first-page", "1"])


def test_rejects_manual_figures_for_non_pdf(tmp_path):
    src = tmp_path / "spec.txt"
    src.write_text("REQ-001 First\nDescription: text.\n")
    cfg = _write_config(tmp_path, manual_figures=[{"node_id": "X", "source_page": 1}])
    with pytest.raises(SystemExit, match="PDF-only"):
        main([str(src), str(tmp_path / "out.reqifz"), "--config", str(cfg)])


def test_reqif_only_writes_plain_file_not_zip(tmp_path):
    src = tmp_path / "spec.txt"
    src.write_text("REQ-001 First\nDescription: text.\n")
    out = tmp_path / "out.reqif"
    cfg = _write_config(tmp_path)

    rc = main([str(src), str(out), "--config", str(cfg), "--reqif-only"])
    assert rc == 0
    assert out.exists()
    assert not zipfile.is_zipfile(out)
    assert out.read_text().startswith('<?xml version="1.0"')


def test_missing_config_exits_with_helpful_message(tmp_path):
    src = tmp_path / "spec.txt"
    src.write_text("REQ-001 First\n")
    with pytest.raises(SystemExit, match="config"):
        main([str(src), str(tmp_path / "out.reqifz")])
