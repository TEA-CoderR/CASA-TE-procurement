

def test_plain_table_numbers_recognised_and_styled(tmp_path):
    """界面表格直接导出（2026-11-25）：数字文字认成数字、条码按文字、€ 列按金额，统一标题条"""
    import xlsx_report as XR
    from openpyxl import load_workbook
    wb = XR.new_book()
    XR.add_plain(wb, "Tab", "Prova", "sub", ["EAN", "Nome", "Colli", "Importo €", "Media"],
                 [["0801234567890", "A", "1,200", "12.50", "1.5"], ["8001", "B", "3", "", "2"]])
    p = tmp_path / "x.xlsx"
    wb.save(p)
    ws = load_workbook(p)["Tab"]
    assert ws["A1"].value == "Prova" and ws["A4"].value == "EAN"
    assert ws["A5"].value == "0801234567890" and ws["C5"].value == 1200 and ws["D5"].value == 12.5
    assert ws["D5"].number_format.startswith("€") and ws["E5"].number_format == "#,##0.00"
    assert ws["D6"].value is None
