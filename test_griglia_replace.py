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
    # 手动搜：pool 里只有 Griglia 没有的
    pool = GR.pool(g, QUOTES)
    assert {c["ean"] for c in pool} == {"8001111000021", "8009999000011", "8002222000024"}
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


class _Row:
    def __init__(self, ws, r):
        self.ws, self.r, self.RowHeight = ws, r, 40

    def Insert(self, Shift=None, CopyOrigin=None):
        ws = self.ws
        ws.v = {((r + 1 if r >= self.r else r), c): x for (r, c), x in ws.v.items()}
        ws.inserted.append(self.r)


class _Col:
    def __init__(self, ws, c):
        self.ws, self.c = ws, c

    def Find(self, What=None, LookIn=None, LookAt=None):
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
        self.comments, self.inserted, self.styles = {}, [], []
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


def test_write_rows_inserts_below_and_keeps_old():
    g = _grid("x.xlsx")
    sug = {r["old"]["ean"]: r["cands"] for r in GR.suggest(g, QUOTES)}
    picks = [(g["items"]["8001111000014"], sug["8001111000014"][0]),
             (g["items"]["8002222000017"], sug["8002222000017"][0])]
    ws = _sheet()
    ex = _Excel(ws)
    done = GR.write_rows("x.xlsx", g, GR.plan_rows(g, picks), excel=ex)
    assert ex.saved
    assert sorted(done) == [("8001111000014", 5), ("8002222000017", 6)]     # 从下往上插：上面的行号不变
    col = [ws.v.get((r, 2)) for r in range(4, 9)]
    assert col == ["8001111000014", "8001111000021", "8002222000017", "8002222000024", "8003333000010"]
    assert ws.v.get((5, 1)) is None and ws.v[(5, 4)] == "COCCOLINO"           # 编号空着、品牌照抄
    assert ws.v[(5, 9)] == 1                                                  # 门店勾选照抄
    assert ws.v[(5, 5)] == "F.LLI PETRILLO SRL"
    assert "8001111000021" in ws.comments[(4, 2)]
    # 旧条码行号对不上（有人插过行）→ 按条码找
    ws2 = _sheet()
    ws2.Rows(1).Insert()
    done2 = GR.write_rows("x.xlsx", g, GR.plan_rows(g, picks[:1]), excel=_Excel(ws2))
    assert done2 == [("8001111000014", 6)] and ws2.v[(6, 2)] == "8001111000021"


def test_add_replacements_backs_up_and_records_done(monkeypatch=None):
    import commands as CMD
    d = tempfile.mkdtemp()
    p = os.path.join(d, "SCHEDA.xlsx")
    with open(p, "wb") as f:
        f.write(b"x")
    g = _grid(p)
    cand = next(r for r in GR.suggest(g, QUOTES) if r["old"]["ean"] == "8001111000014")["cands"][0]
    ex = _Excel(_sheet())
    res = CMD.griglia_add_replacements(g, [(g["items"]["8001111000014"], cand)], by="t", excel=ex)
    assert res["written"] == [("8001111000014", 5)] and os.path.exists(res["backup"])
    done = GR.load_done(d)
    assert done["8001111000014"]["new"] == "8001111000021"
    assert "8001111000014" not in {r["old"]["ean"] for r in CMD.griglia_replacements(g, QUOTES)}
    # Excel 开着 → 不改
    open(os.path.join(d, "~$SCHEDA.xlsx"), "w").close()
    try:
        CMD.griglia_add_replacements(g, [(g["items"]["8002222000017"], cand)], excel=ex)
        assert False
    except RuntimeError:
        pass


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
