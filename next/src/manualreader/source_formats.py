"""Decode original documents without replacing undecodable bytes."""

import codecs
from contextlib import contextmanager
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from bs4.dammit import EncodingDetector


BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
TEXT_ENCODINGS = {
    "utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32",
    "utf-32-le", "utf-32-be", "gb18030", "gbk", "cp1252", "iso8859-1",
    "big5", "shift_jis", "cp932", "euc_kr",
}


def text_encoding(hint):
    if hint is None:
        return None
    if not isinstance(hint, str) or not hint.strip() or len(hint) > 32:
        raise ValueError("请选择纯文本的原始编码")
    try:
        encoding = codecs.lookup(hint).name
    except LookupError as error:
        raise ValueError("不支持所选的文本编码") from error
    if encoding not in TEXT_ENCODINGS:
        raise ValueError("不支持所选的文本编码")
    return "utf-8" if encoding == "utf-8-sig" else encoding


def decode_text(raw, hint=None):
    declared = text_encoding(hint)
    bom, encoding = next(((bom, name) for bom, name in BOMS if raw.startswith(bom)),
                         (b"", declared or "utf-8"))
    if declared and bom and declared not in {encoding, encoding.rsplit("-", 1)[0]}:
        raise UnicodeError("文本编码和字节顺序标记不一致")
    text = raw[len(bom):].decode(encoding)
    if "\x00" in text or text.encode(encoding) != (raw[len(bom):] if bom else raw):
        raise UnicodeError("无法按原编码无损读取文本")
    return text, encoding, len(bom)


def html_soup(raw):
    # Pass bytes so the parser can use the original BOM and HTML charset declaration.
    if any(raw.startswith(bom) for bom, _ in BOMS):
        text, _, _ = decode_text(raw)
        return BeautifulSoup(text, "html.parser")
    if not EncodingDetector.find_declared_encoding(raw, is_html=True):
        try:
            return BeautifulSoup(raw.decode("utf-8"), "html.parser")
        except UnicodeDecodeError:
            pass
    return BeautifulSoup(raw, "html.parser")


class OfficeConversionUnavailable(ValueError):
    pass


@contextmanager
def office_conversion(raw, source_suffix, target_suffix, export_filter):
    binary = os.environ.get("MANUAL_READER_OFFICE_BIN") or shutil.which("soffice")
    if not binary:
        raise OfficeConversionUnavailable("本机未安装可用的 Word 转换程序")
    with TemporaryDirectory(prefix="reader-office-") as directory:
        work = Path(directory)
        profile = work / "profile"
        root = profile / "user"
        root.mkdir(parents=True)
        (root / "registrymodifications.xcu").write_text(
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item><item oor:path="/org.openoffice.Office.Writer/Content/Update">'
            '<prop oor:name="Link" oor:op="fuse"><value>0</value></prop></item></oor:items>',
            encoding="utf-8",
        )
        raw_file = work / ("original"+source_suffix)
        raw_file.write_bytes(raw)
        converted = work / ("original"+target_suffix)
        try:
            subprocess.run([binary, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--norestore",
                            "--convert-to", export_filter, "--outdir", str(work), str(raw_file)],
                           check=True, capture_output=True, timeout=60, cwd=work)
            if not 0 < converted.stat().st_size <= 40_000_000:
                raise ValueError("invalid converted file length")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise OfficeConversionUnavailable("原件已保留，当前未取得可用的转换件") from error
        yield converted


def legacy_word(raw):
    if not raw.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise OfficeConversionUnavailable("文件没有可用的旧版 Word 容器")
    return office_conversion(raw, ".doc", ".docx", "docx")


OFFICE_VECTORS = {".wmf",".emf"}


def office_vector_png(raw, suffix):
    if suffix not in OFFICE_VECTORS or not 18 <= len(raw) <= 10_000_000:
        return None
    from PIL import Image, ImageChops
    try:
        with office_conversion(raw,suffix,".png","png:draw_png_Export") as converted:
            with Image.open(converted) as image:
                base = image.convert("RGBA")
                canvas = Image.new("RGBA",base.size,"white")
                canvas.alpha_composite(base)
                rgb = canvas.convert("RGB")
                box = ImageChops.difference(rgb,Image.new("RGB",rgb.size,"white")).getbbox()
                if box:
                    output = BytesIO()
                    rgb.crop(box).save(output,"PNG")
                    return output.getvalue()
    except (OfficeConversionUnavailable,OSError,Image.DecompressionBombError):
        return None
    return None


WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OFFICE_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def word_xml(raw):
    if len(raw) > 40_000_000 or b"<!DOCTYPE" in raw.upper():
        raise ValueError("Word 文件中的 XML 超出读取范围")
    return ElementTree.fromstring(raw)


def word_text(node):
    return "".join(child.text or "" if child.tag in {WORD+"t", OFFICE_MATH+"t"}
                   else "\t" if child.tag == WORD+"tab" else "\n" if child.tag == WORD+"br" else ""
                   for child in node.iter())


def word_tables(root):
    tables = []
    pending = [(root, None)]
    for index, (node, parent) in enumerate(pending):
        rows, active = [], {}
        headers = []
        for row_index, row in enumerate(node.findall(WORD+"tr")):
            cells, next_merges = [], {}
            if row.find(WORD+"trPr/"+WORD+"tblHeader") is not None:
                headers.append(row_index)
            before = row.find(WORD+"trPr/"+WORD+"gridBefore")
            column = int(before.get(WORD+"val", "0")) if before is not None else 0
            for cell in row.findall(WORD+"tc"):
                span = cell.find(WORD+"tcPr/"+WORD+"gridSpan")
                width = max(1, int(span.get(WORD+"val", "1"))) if span is not None else 1
                vertical = cell.find(WORD+"tcPr/"+WORD+"vMerge")
                nested = []
                for child in cell.findall(WORD+"tbl"):
                    nested.append(len(pending))
                    pending.append((child, {"table":index,"row":row_index,"column":column}))
                info = {"text":"\n".join(word_text(p) for p in cell.findall(WORD+"p")), "colspan":width,
                        "rowspan":1, "column":column, "nested":nested}
                info["grid_span"] = span.get(WORD+"val") if span is not None else None
                info["vertical_merge"] = (vertical.get(WORD+"val") or "continue") if vertical is not None else None
                if (vertical is not None and vertical.get(WORD+"val") != "restart"
                        and column in active and active[column]["colspan"] == width
                        and not info["text"].strip() and not nested):
                    info["continue"] = True
                    active[column]["rowspan"] += 1
                    next_merges[column] = active[column]
                elif vertical is not None:
                    next_merges[column] = info
                cells.append(info)
                column += width
            rows.append(cells)
            active = next_merges
        tables.append({"id":index, "parent":parent, "cells":rows, "header_rows":headers})
    return tables
