import pytest

from reqcast.toon import decode_records, encode_records, estimate_tokens


def test_roundtrip_plain_values_stay_unquoted():
    rows = [{"id": "REQ-001", "body": "Plain text with no special characters"}]
    encoded = encode_records("requirements", ["id", "body"], rows)
    assert encoded == "requirements[1]{id,body}:\n  REQ-001,Plain text with no special characters"
    _key, _fields, decoded = decode_records(encoded)
    assert decoded == rows


def test_values_with_commas_are_quoted_and_roundtrip():
    rows = [
        {"id": "REQ-001", "body": "First requirement."},
        {"id": "REQ-002", "body": "A value with a comma, needs quotes."},
    ]
    encoded = encode_records("requirements", ["id", "body"], rows)
    assert '"A value with a comma, needs quotes."' in encoded
    _key, _fields, decoded = decode_records(encoded)
    assert decoded == rows


def test_values_with_quotes_backslashes_and_newlines_roundtrip():
    rows = [{"id": "0", "body": 'Has "quotes", a \\ backslash, and\na newline.'}]
    encoded = encode_records("requirements", ["id", "body"], rows)
    _key, _fields, decoded = decode_records(encoded)
    assert decoded == rows


def test_header_reports_key_and_fields():
    rows = [{"id": "0", "body": "x"}]
    encoded = encode_records("requirements", ["id", "body"], rows)
    key, fields, _rows = decode_records(encoded)
    assert key == "requirements"
    assert fields == ["id", "body"]


def test_decode_rejects_malformed_header():
    with pytest.raises(ValueError, match="header"):
        decode_records("not a toon block at all")


def test_decode_rejects_row_count_mismatch():
    # Header claims 2 rows but only 1 is present.
    with pytest.raises(ValueError):
        decode_records("requirements[2]{id,body}:\n  0,only one row")


def test_estimate_tokens_is_positive_and_scales_with_length():
    short = estimate_tokens("hi")
    long = estimate_tokens("hello " * 100)
    assert short >= 1
    assert long > short
