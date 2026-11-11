import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import griglia as G
import griglia_replace as GR


def _grid(path=""):
    rows = [["ELENCO DETERSIVI"], [],
            ["Cod.Art.", "Codice EAN", "Descrizione prodotto", "Marca", "Fornitore", "PA MIGLIORE",
             "MELONI", "F.LLI PETRILLO", "FOIANO", "MARLIA"],
            ["101", "8001111000014", "COCCOLINO AMMORBIDENTE 40 LAV", "COCCOLINO", "MELONI WALTER SPA", 2.0, 2.0, 2.2, 1, None],
            ["102", "8002222000017", "SVELTO PIATTI LIMONE 980 ML", "SVELTO", "F.LLI PETRILLO SRL", 1.5, 1.6, 1.5, None, 1],
            ["103", "8003333000010", "SAPONE MARSIGLIA 250 GR", "X", "MELONI WALTER SPA", 1.0, 1.0, None, None, None],
            ["104", "8004444000013", "DETERSIVO PAVIMENTI 1 LT", "Y", "MELONI WALTER SPA", 1.0, 1.0, 1.1, 1, 1]]
    g = G.parse_rows(rows)
    g["path"], g["sheet"] = path, "GRIGLIA"
    return g


QUOTES = {
    "MELONI": {"8001111000021": {"price": 2.1, "pack": 12, "desc": "COCCOLINO AMMORB. 50 LAV"},
               "8009999000011": {"price": 3.0, "pack": 6, "desc": "DIXAN LAVATRICE 30 LAV"},
               "8004444000013": {"price": 1.0, "pack": 12, "desc": "DETERSIVO PAVIMENTI 1 LT"}},
    "petrillo": {"8001111000021": {"price": 2.0, "pack": 12, "desc": "COCCOLINO AMMORBIDENTE 50LAV"},
                 "8002222000024": {"price": 1.4, "pack": 12, "desc": "SVELTO PIATTI LIMONE 1000 ML"},
                 "8004444000013": {"price": 1.1, "pack": 12, "desc": "DETERSIVO PAVIMENTI 1 LT"}},
}


def test_sizes_and_score():
    assert GR.sizes("SVELTO 1,5 LT") == [(1500.0, "ml")]
    assert "lav" in GR.size_text(GR.sizes("COCCOLINO 40 LAV"))
    idf = lambda w: 1.0
    near, _a, _b = GR.score("COCCOLINO AMMORBIDENTE 40 LAV", "COCCOLINO AMMORBIDENTE 50 LAV", "8001111000014", "8001111000021", idf)
    far, _a, _b = GR.score("COCCOLINO AMMORBIDENTE 40 LAV", "DIXAN LAVATRICE 30 LAV", "8001111000014", "8009999000011", idf)
    assert near > 0.8 > far


def test_suggest_finds_size_change_and_skips_done():
    g = _grid()
    out = GR.suggest(g, QUOTES)
    by = {r["old"]["ean"]: r["cands"] for r in out}
    assert set(by) == {"8001111000014", "8002222000017", "8003333000010"}      # 104 还有人做
    c = by["8001111000014"][0]
    assert c["ean"] == "8001111000021" and c["n"] == 2 and c["best"] == 2.0 and c["best_sup"] == "petrillo"
    assert c["size_from"] != c["size_to"]
    assert by["8002222000017"][0]["ean"] == "8002222000024"
    assert not by["8003333000010"]                                           # 没有像的
    out2 = GR.suggest(g, QUOTES, skip={"8001111000014"})
    assert "8001111000014" not in {r["old"]["ean"] for r in out2}
    # 手动搜：pool 里是这周报价的全部商品，Griglia 里已有的标 in_grid
    pool = GR.pool(g, QUOTES)
    assert {c["ean"] for c in pool} == {"8001111000021", "8009999000011", "8002222000024", "8004444000013"}
    assert {c["ean"] for c in pool if c.get("in_grid")} == {"8004444000013"}
    r = GR.rescore(g["items"]["8001111000014"], next(c for c in pool if c["ean"] == "8009999000011"))
    assert r["score"] < GR.SURE


def test_plan_rows_fills_new_row_and_supplier_full_name():
    g = _grid()
    old = g["items"]["8001111000014"]
    cand = next(r for r in GR.suggest(g, QUOTES) if r["old"]["ean"] == "8001111000014")["cands"][0]
    plan = GR.plan_rows(g, [(old, cand)])
    (oe, row, vals), = plan
    col = g["cols"]
    assert oe == old["ean"] and row == old["row"] == 4
    assert vals[col["ean"]] == "8001111000021" and vals[col["code"]] is None
    assert vals[col["supplier"]] == "F.LLI PETRILLO SRL"          # petrillo → Griglia 里的写法
    sc = g["sup_cols"]
    assert vals[sc["MELONI"]] == 2.1 and vals[sc["F.LLI PETRILLO"]] == 2.0


class _Cell:
    def __init__(self, ws, r, c):
        self.ws, self.r, self.c = ws, r, c
        self.NumberFormat = "General"

    @property
    def Value(self):
        return self.ws.v.get((self.r, self.c))

    @Value.setter
    def Value(self, v):
        self.ws.v[(self.r, self.c)] = v

    @property
    def HasFormula(self):
        return str(self.Value or "").startswith("=")

    @property
    def Comment(self):
        return self.ws.comments.get((self.r, self.c))

    def AddComment(self, text):
        self.ws.comments[(self.r, self.c)] = text


class _Style:
    def __init__(self):
        self.Color = None


class _Range:
    def __init__(self, ws, r, c1, c2):
        self.ws, self.r, self.c1, self.c2 = ws, r, c1, c2
        self.Interior, self.Font = _Style(), _Style()
        ws.styles.append(self)

    @property
    def FormulaR1C1(self):
        return (tuple(self.ws.v.get((self.r, c)) for c in range(self.c1, self.c2 + 1)),)

    @FormulaR1C1.setter
    def FormulaR1C1(self, v):
        for c, x in zip(range(self.c1, self.c2 + 1), v[0]):
            self.ws.v[(self.r, c)] = x

    def Copy(self):
        self.ws.clip = self.r

    def PasteSpecial(self, Paste=None):
        self.ws.pasted.append((self.ws.clip, self.r, Paste))


class _Row:
    def __init__(self, ws, r):
        self.ws, self.r, self.RowHeight = ws, r, 40

    def Insert(self, Shift=None, CopyOrigin=None):
        ws = self.ws
        ws.v = {((r + 1 if r >= self.r else r), c): x for (r, c), x in ws.v.items()}
        ws.inserted.append(self.r)

    def Delete(self):
        ws = self.ws
        ws.v = {((r - 1 if r > self.r else r), c): x for (r, c), x in ws.v.items() if r != self.r}
        ws.deleted.append(self.r)


class _Col:
    def __init__(self, ws, c):
        self.ws, self.c = ws, c

    def Find(self, What=None, LookIn=None, LookAt=None, SearchOrder=None, SearchDirection=None):
        if What == "*":
            rows = [r for (r, c), x in self.ws.v.items() if c == self.c and x not in (None, "")]
            return type("H", (), {"Row": max(rows)})() if rows else None
        for (r, c), x in sorted(self.ws.v.items()):
            if c == self.c and str(x) == What:
                return type("H", (), {"Row": r})()
        return None


class _Used:
    Column = 1

    def __init__(self, n):
        self.Columns = type("C", (), {"Count": n})()


class _Ws:
    def __init__(self, rows):
        self.v = {(r, c): x for r, row in enumerate(rows, 1) for c, x in enumerate(row, 1) if x is not None}
        self.comments, self.inserted, self.styles, self.pasted, self.deleted, self.clip = {}, [], [], [], [], None
        self.UsedRange = _Used(10)

    def Cells(self, r, c):
        return _Cell(self, r, c)

    def Rows(self, r):
        return _Row(self, r)

    def Columns(self, c):
        return _Col(self, c)

    def Range(self, a, b):
        return _Range(self, a.r, a.c, b.c)


class _Excel:
    def __init__(self, ws):
        self.ws, self.saved = ws, False
        ex = self

        class WB:
            def Worksheets(self, name):
                return ex.ws

            def Save(self):
                ex.saved = True

            def Close(self, SaveChanges=False):
                pass
        self.Workbooks = type("W", (), {"Open": lambda _s, p: WB()})()


def _sheet():
    return _Ws([["ELENCO DETERSIVI"], [],
                ["Cod.Art.", "Codice EAN", "Descrizione prodotto", "Marca", "Fornitore", "PA MIGLIORE",
                 "MELONI", "F.LLI PETRILLO", "FOIANO", "MARLIA"],
                ["101", "8001111000014", "COCCOLINO AMMORBIDENTE 40 LAV", "COCCOLINO", "MELONI WALTER SPA", 2.0, 2.0, 2.2, 1, None],
                ["102", "8002222000017", "SVELTO PIATTI LIMONE 980 ML", "SVELTO", "F.LLI PETRILLO SRL", 1.5, 1.6, 1.5, None, 1],
                ["103", "8003333000010", "SAPONE MARSIGLIA 250 GR", "X", "MELONI WALTER SPA", 1.0, 1.0, None, None, None]])


def _font(ws, r, attr):
    vals = [getattr(st.Font, attr) for st in ws.styles if st.r == r and hasattr(st.Font, attr)]
    return vals[-1] if vals else None


def test_write_rows_replaces_in_place_and_moves_old_to_bottom_struck():
    """用户 11-09：替换原来的产品行，原来的行移到文件最底部、用线划掉"""
    g = _grid("x.xlsx")
    sug = {r["old"]["ean"]: r["cands"] for r in GR.suggest(g, QUOTES)}
    picks = [(g["items"]["8001111000014"], sug["8001111000014"][0]),
             (g["items"]["8002222000017"], sug["8002222000017"][0])]
    ws = _sheet()
    ex = _Excel(ws)
    done = GR.write_rows("x.xlsx", g, GR.plan_rows(g, picks), excel=ex)
    assert ex.saved
    assert done == [("8002222000017", 5, 7), ("8001111000014", 4, 8)]        # 从下往上：原地换，旧的抄到最下面
    col = [ws.v.get((r, 2)) for r in range(4, 9)]
    assert col == ["8001111000021", "8002222000024", "8003333000010", "8002222000017", "8001111000014"]
    assert ws.v.get((4, 1)) is None and ws.v[(4, 4)] == "COCCOLINO"           # 编号空着、品牌照抄
    assert ws.v[(4, 9)] == 1 and ws.v[(4, 5)] == "F.LLI PETRILLO SRL"          # 门店勾选照旧、供应商换成最便宜那家
    assert ws.v[(8, 1)] == "101" and ws.v[(8, 3)] == "COCCOLINO AMMORBIDENTE 40 LAV"   # 旧行原样在最下面
    assert _font(ws, 8, "Strikethrough") is True and _font(ws, 4, "Strikethrough") is False
    assert (4, 8, -4122) in ws.pasted                                         # 只贴格式，不抄图片
    assert "8001111000021" in ws.comments[(8, 2)]
    # 旧条码行号对不上（有人插过行）→ 按条码找
    ws2 = _sheet()
    ws2.Rows(1).Insert()
    done2 = GR.write_rows("x.xlsx", g, GR.plan_rows(g, picks[:1]), excel=_Excel(ws2))
    assert done2 == [("8001111000014", 5, 8)] and ws2.v[(5, 2)] == "8001111000021"


def test_replacement_already_in_griglia_only_moves_old_row():
    g = _grid("x.xlsx")
    pool = GR.pool(g, QUOTES)
    hit = GR.search(pool, "pavimenti 1lt")                                    # 模糊：不分大小写、1 LT = 1lt
    assert [c["ean"] for c in hit] == ["8004444000013"] and hit[0]["in_grid"]
    assert {c["ean"] for c in GR.search(pool, "COCCOLINO")} == {"8001111000021"}
    ws = _sheet()
    done = GR.write_rows("x.xlsx", g, GR.plan_rows(g, [(g["items"]["8001111000014"], hit[0])]), excel=_Excel(ws))
    assert done == [("8001111000014", None, 6)] and ws.deleted == [4]
    assert [ws.v.get((r, 2)) for r in range(4, 7)] == ["8002222000017", "8003333000010", "8001111000014"]


def _xlsx(path, img=True):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "GRIGLIA"
    ws.append(["ELENCO DETERSIVI"])
    ws.append([])
    ws.append((["Immagine"] if img else []) + ["Cod.Art.", "Codice EAN", "Descrizione prodotto"])
    ws.append(([None] if img else []) + ["101", "8001111000014", "COCCOLINO 40 LAV"])
    ws.append(([None] if img else []) + ["102", "8002222000017", "SVELTO 980 ML"])
    wb.save(path)


def test_add_replacements_edits_source_backs_up_light_and_records(monkeypatch=None):
    """用户 11-09：改同步文件夹里的原件，程序文件夹留一份不带图片的备份；程序里有替换记录"""
    import commands as CMD
    work, sync, bdir = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
    p, src = os.path.join(work, "SCHEDA.xlsx"), os.path.join(sync, "SCHEDA.xlsx")
    _xlsx(p)
    _xlsx(src)
    g = _grid(p)
    old = g["items"]["8001111000014"]
    cand = next(r for r in GR.suggest(g, QUOTES) if r["old"]["ean"] == "8001111000014")["cands"][0]
    opened = []
    ex = _Excel(_sheet())
    orig_open = ex.Workbooks.Open
    ex.Workbooks.Open = lambda path: (opened.append(path), orig_open(path))[1]
    res = CMD.griglia_add_replacements(g, [(old, cand)], by="t", excel=ex, source=src, backup_dir=bdir)
    assert res["written"] == [("8001111000014", 4, 7)] and res["file"] == src
    assert opened == [os.path.abspath(src)]                       # 改的是原件
    from openpyxl import load_workbook
    ws = load_workbook(res["backup"]).active                     # 备份在程序文件夹、图片换成 IMAGE 公式
    assert os.path.dirname(res["backup"]) == bdir
    assert "IMAGE" in ws.cell(4, 1).value and "8001111000014" in ws.cell(4, 1).value
    done = GR.load_done(work)                                     # 记录：旧 → 新、谁、哪个文件第几行
    r = done["8001111000014"]
    assert r["new"] == "8001111000021" and r["old_name"] == old["name"] and r["file"] == src and r["row"] == 4
    assert r["moved_to"] == 7
    assert r["by"] == "t" and r["size"] == "40 → 50 lav"
    # 新条码进了 Griglia 才算替代过（换了一份没加过的 Griglia，旧的又算都不做了）
    assert "8001111000014" in {x["old"]["ean"] for x in CMD.griglia_replacements(g, QUOTES)}
    g["items"]["8001111000021"] = {"ean": "8001111000021", "name": cand["name"]}
    assert "8001111000014" not in {x["old"]["ean"] for x in CMD.griglia_replacements(g, QUOTES)}
    # Excel 开着 → 不改
    open(os.path.join(sync, "~$SCHEDA.xlsx"), "w").close()
    try:
        CMD.griglia_add_replacements(g, [(g["items"]["8002222000017"], cand)], excel=ex, source=src, backup_dir=bdir)
        assert False
    except RuntimeError:
        pass


def test_replaced_row_not_struck_through_and_gets_own_image():
    g = _grid("x.xlsx")
    g["header_row"] = 3
    sug = {r["old"]["ean"]: r["cands"] for r in GR.suggest(g, QUOTES)}
    ws = _sheet()
    for c in range(10, 0, -1):                                    # 第一列前面插一列「Immagine」
        for r in range(1, 7):
            if (r, c) in ws.v:
                ws.v[(r, c + 1)] = ws.v.pop((r, c))
    ws.v[(3, 1)] = "Immagine"
    g2 = dict(g, cols={k: v + 1 for k, v in g["cols"].items()}, sup_cols={k: v + 1 for k, v in g["sup_cols"].items()})
    formulas = {}

    class C(_Cell):
        def __setattr__(self, k, v):
            if k == "Formula2":
                formulas[(self.r, self.c)] = v
            object.__setattr__(self, k, v)
    ws.Cells = lambda r, c: C(ws, r, c)
    GR.write_rows("x.xlsx", g2, GR.plan_rows(g2, [(g2["items"]["8001111000014"], sug["8001111000014"][0])]),
                  excel=_Excel(ws))
    assert "8001111000021" in formulas[(4, 1)] and "IMAGE" in formulas[(4, 1)]   # 原地那行换成新条码的图片
    assert _font(ws, 4, "Strikethrough") is False


def test_sync_from_source_copies_newer_original():
    root, sync = tempfile.mkdtemp(), tempfile.mkdtemp()
    src = os.path.join(sync, "SCHEDA.xlsx")
    _xlsx(src, img=False)
    assert G.sync_from_source(root, src)
    assert os.path.basename(G.find_file(root)) == "SCHEDA.xlsx"
    assert G.sync_from_source(root, src) is None                  # 没变不再复制
    import time
    _xlsx(src, img=False)
    t = time.time() + 30
    os.utime(src, (t, t))
    assert G.sync_from_source(root, src)


def test_each_wholesaler_spelling_scored_and_glued_words_split():
    """用户 11-09：COCCOLINO DILUITO 40 LAV.DELICATOeSOFF. 应该找到 MELONI 的 50 LAV.DELICATO&SOFF.
    （别家写成 COCCOLINO AMM. 3L 50L. DELIC. & SOFFICE，以前只拿第一家的写法算，就找不到了）"""
    assert GR.words("COCCOLINO DILUITO 40 LAV.DELICATOeSOFF.") == ["COCCOLINO", "DILUITO", "DELICATO", "SOFF"]
    rows = [["Cod.Art.", "Codice EAN", "Descrizione prodotto", "Fornitore", "PA MIGLIORE", "MELONI"],
            ["1", "8720181095191", "COCCOLINO DILUITO 40 LAV.DELICATOeSOFF.", "MELONI WALTER SPA", 2.49, 2.49]]
    g = G.parse_rows(rows)
    q = {"NEW GRIECO": {"8721497047263": {"price": 3.3, "desc": "COCCOLINO AMM. 3L 50L. DELIC. & SOFFICE"}},
         "MELONI": {"8721497047263": {"price": 3.09, "desc": "COCCOLINO DILUITO 50 LAV.DELICATO&SOFF."},
                    "8720181593536": {"price": 4.2, "desc": "COCCOLINO WOND.WASH DET.37 LAV.ODOR PRO."}}}
    c = GR.suggest(g, q)[0]["cands"][0]
    assert c["ean"] == "8721497047263" and c["name"] == "COCCOLINO DILUITO 50 LAV.DELICATO&SOFF."
    assert c["score"] >= GR.SURE and c["size_from"] == "40 lav" and c["size_to"] == "50 lav"
    p = next(x for x in GR.pool(g, q) if x["ean"] == "8721497047263")
    assert GR.rescore(g["items"]["8720181095191"], p)["name"] == "COCCOLINO DILUITO 50 LAV.DELICATO&SOFF."


def test_size_change_text_and_lav_written_as_L():
    assert GR.change("COCCOLINO 40 LAV", "COCCOLINO 50 LAV") == ("40 → 50 lav", True)
    assert GR.change("SVELTO 980 ML", "SVELTO 1,03L") == ("980 ml → 1.03 l", True)
    assert GR.change("X 750 ML", "Y 750ML") == ("750 ml", False)
    assert GR.sizes("LYSOFORM LAV.2,43L 54L. COLOR") == [(2430.0, "ml"), (54.0, "lav")]
    assert GR.sizes("LYSOFORM CASA 1100 MLx2 PZ") == [(1100.0, "ml")]


def test_name_diff_marks_only_what_changed():
    def marked(a, b):
        return [t for t, k in GR.diff_tokens(a, b) if k == 1]
    assert marked("COCCOLINO DILUITO 40 LAV.DELICATOeSOFF.", "COCCOLINO DILUITO 50 LAV.DELICATO&SOFF.") == ["40"]
    assert marked("SVELTO PIATTI 980 ML ZENZERO&LIMONE", "SVELTO PIATTI 1,03L ZENZERO & LIMONE") == ["980"]
    assert marked("LYSOFORM DETER.IGIENIZ.40 LAV.CLASSICO", "LYSOFORM DETER.IGIENIZ.36 LAV.COLORI BR.") == ["40", "CLASSICO"]
    assert "".join(t for t, _k in GR.diff_tokens("A 40 LAV.X", "B")) == "A 40 LAV.X"
