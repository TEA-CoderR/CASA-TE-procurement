"""订单文件瘦身：门店文件只留自己那一页、图片换成 IMAGE 公式、浮着的图片删掉（2026-11-26）"""
import os
import re
import zipfile

import order_slim as O


def _book(path):
    ws = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheetViews><sheetView '
          'tabSelected="1" workbookViewId="0"/></sheetViews><sheetData>'
          '<row r="5"><c r="A5" t="inlineStr"><is><t>NO.</t></is></c><c r="B5" t="inlineStr"><is><t>Immagine</t></is></c>'
          '<c r="E5" t="inlineStr"><is><t>Codice EAN</t></is></c></row>'
          '<row r="6"><c r="A6"><v>1</v></c><c r="B6"><f>_xlfn.DISPIMG("ID_X",1)</f></c><c r="E6"><v>8001234567890</v></c></row>'
          '</sheetData><drawing r:id="rId1"/></worksheet>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/xl/workbook.xml" ContentType="a"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="b"/>'
        '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="b"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="b"/>'
        '<Override PartName="/xl/drawings/drawing1.xml" ContentType="d"/></Types>',
        "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="2"/></bookViews>'
        '<sheets><sheet name="GRIGLIA PER TUTTI PDV" sheetId="1" r:id="rId1"/><sheet name="FOIANO" sheetId="2" r:id="rId2"/>'
        '<sheet name="MARLIA" sheetId="3" r:id="rId3"/></sheets><definedNames>'
        '<definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">\'GRIGLIA PER TUTTI PDV\'!$A$5:$K$6</definedName>'
        '<definedName name="_xlnm._FilterDatabase" localSheetId="2" hidden="1">MARLIA!$A$5:$K$6</definedName></definedNames></workbook>',
        "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="x/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="x/worksheet" Target="worksheets/sheet2.xml"/>'
        '<Relationship Id="rId3" Type="x/worksheet" Target="worksheets/sheet3.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": ws, "xl/worksheets/sheet2.xml": ws, "xl/worksheets/sheet3.xml": ws,
        "xl/worksheets/_rels/sheet2.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://x/drawing" Target="../drawings/drawing1.xml"/></Relationships>',
        "xl/drawings/drawing1.xml": "<wsDr/>",
        "xl/drawings/_rels/drawing1.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="x/image" Target="../media/image1.png"/></Relationships>',
        "xl/media/image1.png": "PNG" * 1000,
    }
    with zipfile.ZipFile(path, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)


def test_store_file_keeps_only_its_sheet_and_images_become_formulas(tmp_path):
    p = str(tmp_path / "o.xlsx")
    _book(p)
    r = O.slim(p, drop=lambda n: n != "FOIANO", url="https://img/{ean}.webp")
    assert r["sheets"] == ["FOIANO"] and r["images"] == 1
    z = zipfile.ZipFile(p)
    names = z.namelist()
    assert "xl/worksheets/sheet1.xml" not in names and "xl/worksheets/sheet3.xml" not in names
    assert "xl/media/image1.png" not in names and "xl/drawings/drawing1.xml" not in names
    wb = z.read("xl/workbook.xml").decode()
    assert "MARLIA" not in wb and "GRIGLIA" not in wb and 'activeTab="0"' in wb
    x = z.read("xl/worksheets/sheet2.xml").decode()
    assert "<drawing" not in x and "DISPIMG" not in x and "_xlfn.IMAGE(&quot;" not in x
    assert 'IMAGE("https://img/8001234567890.webp"' in x and 'tabSelected="1"' in x
    ct = z.read("[Content_Types].xml").decode()
    assert "sheet1.xml" not in ct and "drawing1" not in ct


def test_slim_keeps_all_sheets_without_drop(tmp_path):
    p = str(tmp_path / "o.xlsx")
    _book(p)
    r = O.slim(p)
    assert r["sheets"] == ["GRIGLIA PER TUTTI PDV", "FOIANO", "MARLIA"] and r["images"] == 3
    wb = zipfile.ZipFile(p).read("xl/workbook.xml").decode()
    assert len(re.findall("<definedName ", wb)) == 2 and os.path.getsize(p) < 3000
