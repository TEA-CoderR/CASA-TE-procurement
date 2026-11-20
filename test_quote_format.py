"""批发商原文件（不改表头）直接读：结果和操作员改好的文件一样（用户 11-20）"""
from openpyxl import Workbook


def _save(path, rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    return str(path)


def _read(path, tmp_path, monkeypatch):
    import settings
    import service
    monkeypatch.setattr(settings, "app_dir", lambda: str(tmp_path))
    service._QM_MEM.clear()
    a = service._read_price_file(path)
    m = service.quote_matrix(path, lambda *x: None)
    return {e: round(v["price"], 6) for e, v in a.items()}, {e: v["pack"] for e, v in a.items()}, m


def test_meloni_original_headers_same_as_renamed(tmp_path, monkeypatch):
    head = ["barcode_pezzo", "articolo", "ARTICOLI.descrizione", "UDV", "Prezzo", "Ordine"]
    data = [["8001", "1", "SAPONE", 6, 1.5, None], ["8002", "2", "SHAMPOO", 12, "2,10", None]]
    raw = _save(tmp_path / "Listino_01 - Offerta_raw.xlsx", [head] + data)
    prep = _save(tmp_path / "Listino_01 - Offerta.xlsx",
                 [["CODICE EAN", "articolo", "ARTICOLI.descrizione", "UDV", "PREZZO NETTO", "ORDINE"]] + data)
    a, pa, ma = _read(raw, tmp_path, monkeypatch)
    b, pb, mb = _read(prep, tmp_path, monkeypatch)
    assert a == b == {"8001": 1.5, "8002": 2.1}
    assert pa == pb == {"8001": 6, "8002": 12}
    assert ma["price"] == mb["price"] and ma["ok"]


def _petrillo(path, prepared):
    top = [[None, None, None, "F.lli Petrillo & Co."], [None] * 7 + ["Sc.1"], [None] * 7 + [0.0683 if prepared else 0.03]]
    top += [[]] * 4
    head = ["Codice", "ORDINE" if prepared else "Ord.", "RIGA", "DESCRIZIONE", "PZ", "PREZZO", "IVATO",
            "PREZZO NETTO" if prepared else "Vista 3%", "Ivato 3%", None if prepared else "CODICE EAN",
            "FOTO", "Riga Imp.", "Riga Ivat.", "SUBTOT", "SUB IVATO", "CODICE EAN"]
    rows = []
    for ean, pz, p in (("8033532109615", 40, 0.6273), ("8008650435144", 24, 0.904842)):
        h = p * (1 - (0.0683 if prepared else 0.03))
        rows.append(["1055231", 0, 1, "CLENDY " + ean, pz, p, p * 1.22, h, h * 1.22, None,
                     "FOTO", 0, 0, 0, 0, ean])
    return _save(path, top + [head] + rows)


def test_petrillo_always_683_discount_from_raw_or_prepared(tmp_path, monkeypatch):
    raw = _petrillo(tmp_path / "(Prezzi)FlliPetrillo_Base_07_11.xlsx", False)
    prep = _petrillo(tmp_path / "(Prezzi)FlliPetrillo_Base_14_11.xlsx", True)
    a, pa, ma = _read(raw, tmp_path, monkeypatch)
    b, pb, _ = _read(prep, tmp_path, monkeypatch)
    assert a == b == {"8033532109615": round(0.6273 * 0.9317, 6), "8008650435144": round(0.904842 * 0.9317, 6)}
    assert pa == pb == {"8033532109615": 40, "8008650435144": 24}
    assert round(ma["price"]["8033532109615"], 8) == 0.58445541          # 和操作员改好的文件那一格一样


def _grieco(path, prepared):
    head = ["ART. NEW GRIECO", "ORDINE" if prepared else "ORDINE CARTONI", "CODICE EAN" if prepared else "CODICI EAN",
            None, None, None, 47, "CANVASS NEW GRIECO", None, None, None, None, None, None,
            "PREZZO LISTINO", "SCONTO %", "IVA", None if prepared else "PREZZO NETTO"]
    if prepared:
        head += ["PREZZO NETTO"]
    rows = [[None, None, None, "TOP 20"]]
    for ean, pz, desc, o, sc in (("3616304240508", 12, "DOCCIA SH ADIDAS 250", 1.45, 0.25),
                                 ("8054633836392", 6, "IN OMAGGIO 1 CT DI", None, None),       # 赠品行：没有价格
                                 ("8054633836392", 6, "PULIRE PAVIMENTI LT 1 PINO", 0.5, None),
                                 ("8050624487659", 10, "FINISH GEL", 2.95, 25)):               # 折扣写成 25（%）
        net = (o or 0) * (1 - ((sc or 0) / 100 if (sc or 0) > 1 else (sc or 0)))
        r = [1, None, ean, None, pz, 1, desc, None, None, None, None, None, None, None, o, sc, 22, net]
        if prepared:
            r[17] = None
            r.append(round(net * 1.01, 6) if o else 0)
        rows.append(r)
    return _save(path, [head] + rows)


def test_grieco_always_plus_one_percent_from_raw_or_prepared(tmp_path, monkeypatch):
    raw = _grieco(tmp_path / "Canvass 46 New Grieco.xlsx", False)
    prep = _grieco(tmp_path / "Canvass 47 New Grieco.xlsx", True)
    a, _, ma = _read(raw, tmp_path, monkeypatch)
    b, _, mb = _read(prep, tmp_path, monkeypatch)
    want = {"3616304240508": 1.098375, "8054633836392": 0.505, "8050624487659": round(2.95 * 0.75 * 1.01, 6)}
    assert a == b == want                    # 赠品行（没有价格）不当 0 元
    assert {e: round(p, 6) for e, p in ma["price"].items()} == want == {e: round(p, 6) for e, p in mb["price"].items()}


def test_supplier_index_finds_original_order_column(tmp_path):
    import openpyxl
    import service
    p = _petrillo(tmp_path / "(Prezzi)FlliPetrillo_Base_07_11.xlsx", False)
    ws = openpyxl.load_workbook(p).active
    col_ord, index, _ = service._supplier_index(ws, p)
    assert col_ord == 2 and index == {"8033532109615": 9, "8008650435144": 10}
    g = _grieco(tmp_path / "Canvass 46 New Grieco.xlsx", False)
    ws = openpyxl.load_workbook(g).active
    col_ord, index, _ = service._supplier_index(ws, g)
    assert col_ord == 2 and index["3616304240508"] == 3


def test_grieco_pack_column_without_header_found_without_formulas(tmp_path, monkeypatch):
    raw = _grieco(tmp_path / "Canvass 46 New Grieco.xlsx", False)
    _a, pack, m = _read(raw, tmp_path, monkeypatch)
    assert pack == {"3616304240508": 12, "8054633836392": 6, "8050624487659": 10}
    assert m["pack_col"] == 5


def test_price_sheet_found_when_not_the_active_sheet(tmp_path, monkeypatch):
    wb = Workbook()
    ws = wb.active
    ws.title = "Novita"
    ws.append(["Nuovi articoli della settimana"])
    ws2 = wb.create_sheet("LISTINO x Famiglie")
    ws2.append(["barcode_pezzo", "articolo", "ARTICOLI.descrizione", "UDV", "Prezzo", "Ordine"])
    for i in range(30):
        ws2.append([str(8000000000000 + i), str(i), "ART %d" % i, 6, 1.0 + i / 10, None])
    wb.active = 0
    p = str(tmp_path / "Listino_01 - Offerte.xlsx")
    wb.save(p)
    a, pack, m = _read(p, tmp_path, monkeypatch)
    assert len(a) == 30 and a["8000000000005"] == 1.5 and m["ok"]
    import openpyxl
    import quote_format as QF
    assert QF.order_sheet(openpyxl.load_workbook(p)).title == "LISTINO x Famiglie"


def test_unreadable_quote_says_what_headers_it_saw(tmp_path):
    import pytest
    import service
    p = _save(tmp_path / "strano.xlsx", [["Codice", "Descr", "Costo"], ["1", "A", 2]])
    with pytest.raises(ValueError) as e:
        service._read_price_file(p)
    assert "CODICE" in str(e.value) and "COSTO" in str(e.value)


def test_prepare_grieco_like_operator_inserts_net_column_and_shifts_formulas(tmp_path, monkeypatch):
    """原报价（存成 .xlsx、带公式）→ 和操作员整理的一样：表头改名、原净价表头清掉、右边插 PREZZO NETTO，后面的公式跟着挪"""
    import openpyxl
    import quote_prepare as QP
    wb = Workbook()
    ws = wb.active
    ws.title = "CANVASS AGGIORNATO"
    ws.append(["ART. NEW GRIECO", "ORDINE CARTONI", "CODICI EAN", None, None, None, 46, "CANVASS NEW GRIECO",
               None, None, None, None, None, None, "PREZZO LISTINO", "SCONTO %", "IVA", "PREZZO NETTO",
               "PREZZO CON IVA", "ORDINE TOTALE"])
    ws.merge_cells("H1:L1")
    for i, (ean, o, sc) in enumerate((("3616304240508", 1.45, 0.25), ("8050624487659", 4.2, None)), 2):
        ws.append([1, None, ean, None, 12, 1, "ART", None, None, None, None, None, None, None, o, sc, 22,
                   f"=O{i}*(1-P{i})", f"=R{i}*(1+Q{i}%)", f"=(B{i}*E{i}*S{i})"])
    ws.auto_filter.ref = "A1:T3"
    ws.column_dimensions["S"].width = 7.1
    src = str(tmp_path / "Canvass 48 New Grieco.xlsx")
    wb.save(src)
    out, what = QP.prepare(src, str(tmp_path / "ok"))
    assert out.endswith("Canvass 48 New Grieco.xlsx") and out != src and what
    ws = openpyxl.load_workbook(out).active
    assert [ws.cell(1, c).value for c in (2, 3, 18, 19, 20, 21)] == \
        ["ORDINE", "CODICE EAN", None, "PREZZO NETTO", "PREZZO CON IVA", "ORDINE TOTALE"]
    assert ws["S2"].value == 1.098375 and ws["S3"].value == 4.242
    assert ws["R2"].value == "=O2*(1-P2)" and ws["T2"].value == "=R2*(1+Q2%)" and ws["U2"].value == "=(B2*E2*T2)"
    assert ws.auto_filter.ref == "A1:U3" and [str(r) for r in ws.merged_cells.ranges] == ["H1:L1"]
    assert round(ws.column_dimensions["T"].width, 1) == 7.1
    # 整理过的再放一次不动
    assert QP.prepare(out, str(tmp_path / "ok2")) == (out, "")
    a, pack, _m = _read(out, tmp_path, monkeypatch)
    assert a == {"3616304240508": 1.098375, "8050624487659": 4.242} and pack["3616304240508"] == 12


def test_prepare_petrillo_and_meloni_like_operator(tmp_path, monkeypatch):
    import openpyxl
    import quote_prepare as QP
    raw = _petrillo(tmp_path / "(Prezzi)FlliPetrillo_Base_21_11.xlsx", False)
    out, _ = QP.prepare(raw, str(tmp_path / "ok"))
    ws = openpyxl.load_workbook(out).active
    assert ws["B8"].value == "ORDINE" and ws["H8"].value == "PREZZO NETTO" and ws["J8"].value is None
    assert ws["H3"].value == 0.0683 and ws["H9"].value == 0.58445541 and ws["P8"].value == "CODICE EAN"
    assert QP.prepare(out, str(tmp_path / "ok2"))[1] == ""
    head = ["barcode_pezzo", "articolo", "ARTICOLI.descrizione", "UDV", "Prezzo", "Ordine"]
    mel = _save(tmp_path / "Listino_01 - Offerte.xlsx", [head, ["8001", "1", "SAPONE", 6, 1.5, None]])
    out, _ = QP.prepare(mel, str(tmp_path / "ok"))
    ws = openpyxl.load_workbook(out).active
    assert [c.value for c in ws[1]] == ["CODICE EAN", "articolo", "ARTICOLI.descrizione", "UDV", "PREZZO NETTO", "ORDINE"]
    assert ws["E2"].value == 1.5
