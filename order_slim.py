"""
订单文件「瘦身」（2026-11-26 用户：给每家店发的订单文件里不要有别家店的内容；图片全部换成 IMAGE 公式，
生成的订单文件不会越来越大）。不依赖界面，直接改 xlsx 里的 XML（不经过 Excel / openpyxl，格式、公式原样）。

slim(路径, drop=要删的工作表, url=图片网址模板)：
  ① drop 给了：这些工作表整页删掉（workbook / 关系 / 内容类型 / 只属于那页的命名区域都跟着删，
     页序号 localSheetId 重新排；打开时停在第一页）
  ② 每一页：表头写 Immagine / Foto 的那一列，商品行换成 =IFERROR(_xlfn.IMAGE(按这行条码的网址),"")
     （条码取同一页「Codice EAN」那一列）；浮在格子上的图片（drawing）删掉
  ③ WPS 的单元格图片（cellimages.xml，=DISPIMG(...)）只留还有格子在用的（左上角的公司标志），
     没人用的图片文件（xl/media）删掉
"""
import html
import os
import re
import zipfile

IMG_HEADS = ("IMMAGINE", "IMMAGINI", "FOTO", "IMAGE", "IMG", "PICTURE")
EAN_HEADS = ("CODICE EAN", "EAN", "COD. EAN", "COD.EAN", "BARCODE", "CODICE A BARRE")
HEAD_ROWS = 15                         # 表头在前 15 行里找

_CELL_RE = re.compile(r'<c\b[^>]*\br="([A-Z]+)(\d+)"[^>]*?(?:/>|>.*?</c>)', re.S)


def _col_num(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n


def _norm(t):
    return re.sub(r"\s+", " ", str(t or "")).strip().upper()


def _sst(files):
    x = files.get("xl/sharedStrings.xml")
    if not x:
        return []
    x = x.decode("utf-8")
    return [html.unescape("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", si, re.S)))
            for si in re.findall(r"<si\b[^>]*>(.*?)</si>", x, re.S)]


def _text(cell, sst):
    tag = cell.split(">")[0]
    v = re.search(r"<v>(.*?)</v>", cell, re.S)
    if 't="s"' in tag:
        try:
            return sst[int(v.group(1))] if v else ""
        except (ValueError, IndexError):
            return ""
    if 't="inlineStr"' in tag:
        return html.unescape("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", cell, re.S)))
    return html.unescape(v.group(1)) if v else ""


def _rels_path(part):
    d, f = os.path.split(part)
    return f"{d}/_rels/{f}.rels"


def _target(base, t):
    """关系里的 Target → zip 里的路径"""
    if t.startswith("/"):
        return t[1:]
    parts = os.path.dirname(base).split("/")
    for seg in t.split("/"):
        if seg == "..":
            parts = parts[:-1]
        elif seg and seg != ".":
            parts.append(seg)
    return "/".join(p for p in parts if p)


def _rels(files, part):
    x = files.get(_rels_path(part))
    if not x:
        return []
    out = []
    for m in re.finditer(r"<Relationship\b[^>]*/?>", x.decode("utf-8")):
        tag = m.group(0)
        rid = re.search(r'\bId="([^"]+)"', tag)
        tgt = re.search(r'\bTarget="([^"]+)"', tag)
        typ = re.search(r'\bType="([^"]+)"', tag)
        ext = 'TargetMode="External"' in tag
        if rid and tgt:
            out.append((rid.group(1), typ.group(1) if typ else "", tgt.group(1), ext, tag))
    return out


def _sheets(files):
    """[(名字, 路径, <sheet …/> 原文)]，按工作簿里的顺序"""
    wb = files["xl/workbook.xml"].decode("utf-8")
    rid2 = {r[0]: _target("xl/workbook.xml", r[2]) for r in _rels(files, "xl/workbook.xml")}
    out = []
    for m in re.finditer(r"<sheet\b[^>]*/>", wb):
        tag = m.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        rid = re.search(r'\br:id="([^"]+)"', tag)
        if name and rid and rid.group(1) in rid2:
            out.append((html.unescape(name.group(1)), rid2[rid.group(1)], tag))
    return out


# ===================================================================== ① 删工作表
def _drop_sheets(files, drop):
    sheets = _sheets(files)
    gone = [s for s in sheets if drop(s[0])]
    if not gone or len(gone) == len(sheets):
        return
    old_idx = {s[0]: i for i, s in enumerate(sheets)}
    kept = [s for s in sheets if s not in gone]
    new_idx = {old_idx[s[0]]: i for i, s in enumerate(kept)}
    gone_names = [s[0] for s in gone]
    wb = files["xl/workbook.xml"].decode("utf-8")
    for s in gone:
        wb = wb.replace(s[2], "", 1)

    def fix_name(m):
        tag = m.group(0)
        loc = re.search(r'\blocalSheetId="(\d+)"', tag)
        if loc:
            i = int(loc.group(1))
            if i not in new_idx:
                return ""
            tag = tag.replace(loc.group(0), f'localSheetId="{new_idx[i]}"', 1)
        body = html.unescape(re.sub(r"<[^>]+>", "", tag))
        if any(re.search(r"(^|[^A-Za-z0-9_.])'?" + re.escape(n) + r"'?!", body) for n in gone_names):
            return ""
        return tag
    wb = re.sub(r"<definedName\b[^>]*>.*?</definedName>", fix_name, wb, flags=re.S)
    wb = re.sub(r"<definedNames>\s*</definedNames>", "", wb)
    wb = re.sub(r'\b(activeTab|firstSheet)="\d+"', r'\1="0"', wb)
    files["xl/workbook.xml"] = wb.encode("utf-8")
    # 工作簿关系、内容类型
    gone_paths = {s[1] for s in gone}
    wr = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
    for r in _rels(files, "xl/workbook.xml"):
        if _target("xl/workbook.xml", r[2]) in gone_paths:
            wr = wr.replace(r[4], "", 1)
    files["xl/_rels/workbook.xml.rels"] = wr.encode("utf-8")
    for p in gone_paths:
        files.pop(p, None)
        files.pop(_rels_path(p), None)
    # 第一页设成选中的那页
    for i, (_n, p, _t) in enumerate(kept):
        x = files[p].decode("utf-8")
        x = re.sub(r'\s+tabSelected="1"', "", x)
        if i == 0:
            x = re.sub(r"<sheetView\b", '<sheetView tabSelected="1"', x, count=1)
        files[p] = x.encode("utf-8")
    # docProps/app.xml 里的页名（Excel 不看，删了也不会报错；留着就对不上，干脆删掉这两项）
    app = files.get("docProps/app.xml")
    if app:
        a = app.decode("utf-8")
        a = re.sub(r"<HeadingPairs>.*?</HeadingPairs>", "", a, flags=re.S)
        a = re.sub(r"<TitlesOfParts>.*?</TitlesOfParts>", "", a, flags=re.S)
        files["docProps/app.xml"] = a.encode("utf-8")


# ===================================================================== ② 图片列换成 IMAGE 公式
def image_formula(ean, url):
    u = url.format(ean=ean).replace('"', "")
    return f'IFERROR(_xlfn.IMAGE("{u}","",3,48,48),"")'


def _ean_text(v):
    t = str(v or "").strip()
    if re.fullmatch(r"\d+\.0+", t):
        t = t.split(".")[0]
    if re.fullmatch(r"\d+(\.\d+)?[eE]\+\d+", t):
        try:
            t = str(int(float(t)))
        except ValueError:
            return ""
    return t if re.fullmatch(r"\d{6,14}", t) else ""


def _images_to_formula(xml, sst, url):
    """这一页：找表头的图片列 / 条码列，商品行的图片格写 IMAGE 公式；返回 (新 XML, 写了几格)"""
    head = None
    for cm in _CELL_RE.finditer(xml):
        r = int(cm.group(2))
        if r > HEAD_ROWS:
            break
        t = _norm(_text(cm.group(0), sst))
        if t and any(t == h or t.startswith(h + " ") for h in IMG_HEADS):
            head = (r, cm.group(1))
            break
    if not head:
        return xml, 0
    hr, img_col = head
    ean_col = None
    for cm in _CELL_RE.finditer(xml):
        if int(cm.group(2)) == hr:
            t = _norm(_text(cm.group(0), sst))
            if t in EAN_HEADS or "EAN" in t.split():
                ean_col = cm.group(1)
                break
    if not ean_col:
        return xml, 0
    eans = {}
    for cm in _CELL_RE.finditer(xml):
        r = int(cm.group(2))
        if r > hr and cm.group(1) == ean_col:
            e = _ean_text(_text(cm.group(0), sst))
            if e:
                eans[r] = e
    n = 0
    for r, e in eans.items():
        ref = f"{img_col}{r}"
        f = html.escape(image_formula(e, url), quote=False)
        m = re.search(r'<c\b[^>]*\br="' + ref + r'"[^>]*?(?:/>|>.*?</c>)', xml, re.S)
        if m:
            st = re.search(r'\bs="(\d+)"', m.group(0).split(">")[0])
            new = f'<c r="{ref}"' + (f' s="{st.group(1)}"' if st else "") + f' t="str"><f>{f}</f></c>'
            xml = xml[:m.start()] + new + xml[m.end():]
            n += 1
            continue
        rm = re.search(r'<row\b[^>]*\br="' + str(r) + r'"[^>]*?(/>|>(.*?)</row>)', xml, re.S)
        if not rm:
            continue
        new = f'<c r="{ref}" t="str"><f>{f}</f></c>'
        if rm.group(1) == "/>":
            xml = xml[:rm.start()] + rm.group(0)[:-2] + ">" + new + "</row>" + xml[rm.end():]
        else:
            inner = rm.group(2)
            pos = len(inner)
            for c2 in re.finditer(r'<c\b[^>]*\br="([A-Z]+)\d+"', inner):
                if _col_num(c2.group(1)) > _col_num(img_col):
                    pos = c2.start()
                    break
            xml = xml[:rm.start(2) + pos] + new + xml[rm.start(2) + pos:]
        n += 1
    return xml, n


def _drop_drawings(files, sheet_path):
    """浮在格子上的图片：删掉这页的 <drawing>（批注用的 vml 不动）"""
    rels = _rels(files, sheet_path)
    dr = [r for r in rels if r[1].endswith("/drawing")]
    if not dr:
        return
    x = files[sheet_path].decode("utf-8")
    for r in dr:
        x = re.sub(r'<drawing\b[^>]*r:id="' + re.escape(r[0]) + r'"[^>]*/>', "", x)
    files[sheet_path] = x.encode("utf-8")
    rx = files[_rels_path(sheet_path)].decode("utf-8")
    for r in dr:
        rx = rx.replace(r[4], "", 1)
    files[_rels_path(sheet_path)] = rx.encode("utf-8")


# ===================================================================== ③ 清掉没人用的图片
def _prune_cellimages(files, sheet_paths):
    ci = "xl/cellimages.xml"
    if ci not in files:
        return
    used = set()
    for p in sheet_paths:
        used.update(re.findall(r"DISPIMG\((?:&quot;|\")([^&\"]+)", files[p].decode("utf-8")))
    x = files[ci].decode("utf-8")
    keep_rids = set()

    def one(m):
        blk = m.group(0)
        nm = re.search(r'<xdr:cNvPr\b[^>]*\bname="([^"]+)"', blk)
        if nm and nm.group(1) in used:
            keep_rids.update(re.findall(r'r:embed="([^"]+)"', blk))
            return blk
        return ""
    x = re.sub(r"<etc:cellImage\b.*?</etc:cellImage>", one, x, flags=re.S)
    files[ci] = x.encode("utf-8")
    rp = _rels_path(ci)
    if rp in files:
        rx = files[rp].decode("utf-8")
        for r in _rels(files, ci):
            if r[0] not in keep_rids:
                rx = rx.replace(r[4], "", 1)
        files[rp] = rx.encode("utf-8")


def _prune_orphans(files):
    """没有任何关系指着的 drawing / 图片文件删掉（反复几次：删了 drawing，它的图片也没人用了）"""
    for _ in range(4):
        pointed = set()
        for k in list(files):
            if k.endswith(".rels"):
                src = k.replace("_rels/", "")[:-5]
                for r in _rels(files, src):
                    if not r[3]:
                        pointed.add(_target(src, r[2]))
        drop = [k for k in files
                if (k.startswith("xl/media/") or re.match(r"xl/drawings/drawing\d+\.xml$", k))
                and k not in pointed]
        if not drop:
            break
        for k in drop:
            files.pop(k, None)
            files.pop(_rels_path(k), None)


def _fix_content_types(files):
    ct = files.get("[Content_Types].xml")
    if not ct:
        return
    x = ct.decode("utf-8")

    def ov(m):
        p = re.search(r'PartName="/([^"]+)"', m.group(0))
        return m.group(0) if not p or p.group(1) in files else ""
    x = re.sub(r"<Override\b[^>]*/>", ov, x)
    files["[Content_Types].xml"] = x.encode("utf-8")


# =====================================================================
def slim(path, drop=None, url=None, out=None):
    """
    path：订单文件（xlsx）；drop：要删的页（名字列表，不分大小写 / 空格；或者 函数(页名) → True 删），None = 全留；
    url：图片网址模板（{ean}）。改好写回 out（默认原文件）。返回 {"images": 写了几格, "sheets": 留下的页}
    """
    import product_images as PI
    url = url or PI.DEFAULT_URL
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        files = {i.filename: z.read(i.filename) for i in infos}
    order = [i.filename for i in infos]
    if drop:
        if not callable(drop):
            names = {_norm(n) for n in drop}
            drop_fn = lambda n: _norm(n) in names          # noqa: E731
        else:
            drop_fn = drop
        _drop_sheets(files, drop_fn)
    sst = _sst(files)
    sheets = _sheets(files)
    n = 0
    for _name, p, _t in sheets:
        if p not in files:
            continue
        x, k = _images_to_formula(files[p].decode("utf-8"), sst, url)
        files[p] = x.encode("utf-8")
        n += k
        _drop_drawings(files, p)
    _prune_cellimages(files, [p for _n, p, _t in sheets if p in files])
    _prune_orphans(files)
    _fix_content_types(files)
    if "xl/calcChain.xml" in files:                 # 删了页 / 改了公式：让 Excel 自己重排
        files.pop("xl/calcChain.xml")
        wr = files["xl/_rels/workbook.xml.rels"].decode("utf-8")
        wr = re.sub(r'<Relationship\b[^>]*calcChain[^>]*/>', "", wr)
        files["xl/_rels/workbook.xml.rels"] = wr.encode("utf-8")
        _fix_content_types(files)
    out = out or path
    tmp = out + ".slim"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for k in order:
            if k in files:
                zo.writestr(k, files[k])
    os.replace(tmp, out)
    return {"images": n, "sheets": [s[0] for s in _sheets(files)]}
