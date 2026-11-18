from base64 import b64encode

import pytest
from docx import Document
from openpyxl import Workbook

from manualreader.source_formats import decode_text
from manualreader.stages.step02.original import view
from manualreader.stages.step03.cache import Cache
from manualreader.stages.step03.documents import parse_document
from reader_import import SourceImporter
from reader_workspace import Workspace
from source_parser import parse


@pytest.mark.parametrize("encoding,hint", [
    ("utf-8-sig", None), ("utf-16", None), ("utf-32", None), ("gb18030", "GB18030"),
    ("cp1252", "Windows-1252"),
])
def test_text_import_keeps_original_bytes_and_line_offsets(tmp_path, encoding, hint):
    value = "Résumé €\r\nSecond line\r\n"
    raw = value.encode(encoding)
    reader = Workspace(tmp_path / "reader")
    request = {"filename":"source.txt", "content_base64":b64encode(raw).decode()}
    if hint:
        request["encoding"] = hint
    source = SourceImporter(reader).import_source(request)
    assert source["import_status"] == "ready"
    assert (reader.files / source["id"] / source["stored_name"]).read_bytes() == raw
    assert source["document"]["passages"][0]["en"] == value
    assert SourceImporter(reader).import_source(request)["id"] == source["id"]
    technical = parse_document(raw, ".txt", Cache(tmp_path / "cache"), None, encoding=hint)
    codec = technical["encoding"]
    for item in technical["assets"]:
        where = item["locator"]
        assert raw[where["byte_start"]:where["byte_end"]].decode(codec) == item["text"]
    if not hint:
        assert view(raw, ".txt")[1][0]["text"] == value.splitlines()[0]


def test_decode_rejects_truncated_bom_and_mismatched_explicit_encoding():
    with pytest.raises(UnicodeError):
        decode_text(b"\xff\xfe\x01")
    with pytest.raises(UnicodeError):
        decode_text("Title".encode("utf-16"), "cp1252")
    with pytest.raises(ValueError):
        decode_text(b"ordinary", "unicode_escape")


def test_declared_html_encoding_and_hidden_table_preserve_the_correct_structure(tmp_path):
    raw = ('<meta charset="windows-1252"><nav>Menu</nav><table hidden><tr><td>Hidden</td></tr></table>'
           '<main><h1>Café</h1><table><tr><th colspan="2">Prix</th></tr><tr><td>Article</td><td>€5</td></tr>'
           '</table></main><aside><p>Footnote €2</p></aside>').encode("cp1252")
    file = tmp_path / "test.html"
    file.write_bytes(raw)
    reader = parse(file)
    assert any(p["en"] == "Café" for p in reader["passages"])
    assert any(p["en"] == "Footnote €2" for p in reader["passages"])
    table = next(p for p in reader["passages"] if p["kind"] == "table")
    assets = parse_document(raw, ".html", Cache(tmp_path / "cache"), None)["assets"]
    content = next(a for a in assets if a["kind"] == "table")
    assert table["table_cells"][0][0]["colspan"] == content["structure"]["rows"][0][0]["colspan"] == 2
    assert content["structure"]["rows"][1][1]["text"] == "€5"
    assert assets[0]["role"] == "page_furniture"


def test_docx_and_xlsx_keep_native_coordinates_and_cell_content(tmp_path):
    doc = Document()
    t = doc.add_table(rows=3, cols=3)
    t.cell(0,0).merge(t.cell(0,1)).text = "Header"
    t.cell(1,0).merge(t.cell(2,0)).text = "Vertical"
    t.cell(1,1).add_table(rows=1, cols=1).cell(0,0).text = "Nested"
    word = tmp_path / "word.docx"
    doc.save(word)
    rows = parse(word)["passages"]
    root, nested = [r for r in rows if r["kind"] == "table"]
    assert root["table_cells"][0][0]["colspan"] == 2
    assert root["table_cells"][1][0]["rowspan"] == 2
    assert root["table_cells"][2][0]["continue"] is True
    assert nested["en"] == "Nested" and nested["source_location"]["parent_cell"]["column"] == 1

    book = Workbook()
    book.active["B2"] = "=1+2"
    sheet = tmp_path / "source.xlsx"
    book.save(sheet)
    workbook = parse(sheet)
    cells = next(r for r in workbook["passages"] if r["kind"] == "table")
    assert cells["rows"][1] == ["B2", "", "`=1+2`"]
    assert cells["source_location"]["cells"] == ["B2"]


def test_vtt_keeps_time_and_visible_text(tmp_path):
    file = tmp_path / "captions.vtt"
    file.write_text("WEBVTT\n\n00:00.100 --> 00:02.200\nFirst cue.\n", encoding="utf-8")
    p = parse(file)["passages"][0]
    assert p["en"] == "First cue."
    assert p["source_location"]["start_seconds"] == .1
    assert p["source_location"]["end_seconds"] == 2.2
