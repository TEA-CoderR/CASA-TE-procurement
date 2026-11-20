# Griglia：给「停售」的商品找替代品

2026-11-09 用户：Griglia 里有 34 个商品这周所有批发商都不卖了。很多其实是换了规格（40 洗 → 50 洗、950 ml → 1000 ml），
要在新报价里找出来，人核对后加进 Griglia。用户定：**旧的那行留着，下面加一行新的**。

## 怎么用
DETERSIVO采购 → Griglia →「停售」那张表 →「🔄 找替代商品」。
窗口左边一行一个不做了的商品，右边是选中那一行：旧商品（图 + 资料）、替代成（图 + 各家价格）、候选列表。
- 像不像 ≥ 80% 的先勾上；每一个都要看一眼。
- 候选不对：右边选别的，或者「在这周报价里搜…」输入名称里的词 / 条码，在这周报价里所有 Griglia 没有的商品里找。
- 选「— 不替代」就不加。
- 搜索（用户 11-11）：这周报价里的全部商品都搜，Griglia 里已经有的也列出来（标「Griglia 里已有」）；不分大小写、标点，词可以只写一部分（ULTIM = ULTIMATE、540ml = 540 ML），条码可以只输开头。
- 划了删除线的行（不做了挪到最下面的）不算 Griglia 商品，不会出现在「停售」。
- 「加进 Griglia（n）」→ 确认 → 写好以后 Griglia 页自动刷新。

## 写进 Griglia 的时候做了什么（`commands.griglia_add_replacements`）
1. **改哪份**（用户 11-09）：「换 Griglia…」从同步文件夹选的，程序记下原件（settings griglia.source），**直接改原件**；
   没记下原件的才改工作文件夹「0 Griglia」那份。程序读的是「0 Griglia」里的副本：原件比副本新就自动重新复制（`griglia.sync_from_source`，刷新 Griglia 页时），
   改完原件也马上复制回副本。Griglia 页标题右边写「· 同步文件夹」，鼠标提示写原件路径。
2. 要改的文件被 Excel 打开着（有 `~$` 锁文件）→ 不改，提示先关掉。
3. 先在**程序文件夹 backup\griglia** 存一份备份，留最近 5 份。备份不带嵌进去的图片：图片那一列（表头 Immagine / Foto）写成
   `=IFERROR(_xlfn.IMAGE(网址),"")`（`griglia_replace.light_copy`，openpyxl 读的时候跳过图片），几百 MB → 几 MB。
4. 用 Excel 本身改（`griglia_replace.write_rows`，win32com）：图片、格式、公式都保留。不用 openpyxl 存原件（会丢图片）。
   **（用户 11-11 改）原地替换、旧行挪到最下面划线**（和 Griglia 里原来的做法一样）：
   ① 旧行抄一份到最下面（EAN 列最后一个有内容的行下面）：只贴格式（不抄商品图片）+ 内容按 R1C1 照抄，整行删除线、字变灰，旧条码格加批注；
   ② 原来那一行原地换成新商品：条码（文字格式）/ 名称 / 箱规 / PREZZO NETTO、PA MIGLIORE（最低价）/ Fornitore（最便宜那家，用 Griglia 里那家的写法）/
   各批发商价格（这周没报价的那家清空），编号（COD.ART.）清空，改过的格子浅蓝底，去掉删除线，图片那一格写新条码的 =IMAGE(网址)；
   分类 / 品牌 / 门店勾选 / 公式照旧（别的表按行引用的也跟着变成新商品）；
   ③ 新商品 Griglia 里已经有（手动搜出来、标「Griglia 里已有」的）：只把旧行挪到最下面，原来那行删掉。
5. 行号对不上（有人插过行）→ 按条码整格再找一次；还找不到就跳过、写日志。
6. 记进「0 Griglia\_替代记录.json」：旧条码 → 新条码、旧 / 新名称、规格变化、谁、什么时候、改的文件、第几行、最低价。
   Griglia 页「停售」表上「替换记录（n）」按钮看全部记录（可以搜、可以打开那个文件）。
   **新条码在现在这份 Griglia 里**才算替代过（换了一份没加过的 Griglia，旧商品又会出现在「停售」）。
7. 审计 logs/audit.jsonl。

## 怎么算相似度（`griglia_replace.score`，2026-11-20 第二版）
用户 11-20：「LAV. DIXAN CLASSICO 40 MISURINI」排第一的是 130 MISURINI → 重写：
- 名字（0.7）：两边每个词找另一边最像的词，少见的词分量重（idf）；缩写算对上（DETER = DETERSIVO 0.85，三个字母的 0.6），
  连写算对上（MASTRO LINDO = MASTROLINDO）；防晒 SPF30 / PF30 / FP 30 当一个词（FP30 ≠ FP50）；+ 三个字母一组的相似度（0.3）；
- 前两个词（多半是品牌）一个都对不上 → ×0.4（缩写要前 5 个字母一样才算，PAVI ≠ PAVIM）；
- 规格（`size_adj`，−0.25 … +0.25）：单位统一（l → ml、kg → g；MISURINI / MIS / DOSI = 洗几次 lav；「52+8L」= 60 洗、「12+4PZ」= 16 件）；
  同一种规格 一样多 +0.15、差 20% 以内 +0.08、差 40% 以内 +0.02、差 55% 以内 −0.04、更多 −0.12；
  一个按 ml 一个按洗几次 −0.10；旧的写了规格候选没写 −0.04；张数写法不一样（22 PANNI ↔ PZ.20）按一半算；
  几瓶装：都写了一样 +0.06 / 不一样 −0.06，只有一边是几瓶装 −0.12（PZ.1 = 单个）；没写单位的数（SVELTO 1030）和另一边对上 +0.06；
- 条码前 7 位一样 +0.06，前 5 位 +0.03（厂家换前缀很常见，只加一点）；这周新上架 +0.03（不盖过规格）。
- **Griglia 里已经有的商品也是候选**（标「Griglia 里已有」，选它 = 旧的那行只移到最下面划线）：SVELTO 980 ML x 3 → 1000 ML x 3、
  COCCOLINO DILUITO 40 → 50 LAV 这种替代品早就在 Griglia 里了。
先按前三个词 + 连写 + 条码前缀粗筛；太多时（> `PRE_TOP` 120）先按共同的词粗排（品牌对上的优先），只细算前 120 个。
每个旧商品最多 5 个候选（`TOP`），低于 0.35 不要（`MIN_SCORE`）。
真实数据（11-20 这周 4 份报价、49 个停售）：DIXAN 40 MISURINI 100% vs 130 MISURINI 73%；用时云端约 1.5 秒（Griglia 页统计完后台先算好）。

测试：test_griglia_replace.py（假 Excel）。

## 划线的商品（2026-11-11）

窗口里也列出 Griglia 里已经划线、而且这周批发商都不卖了的商品（11-11 第二次，用户：还有人卖的是自己决定不订，不用找替代；分「批发商都没货 / Griglia 已停用」两种增加歧义 → 统一叫「停售」）。划线的只在「原商品」的鼠标提示里写第几行已经划线。任何时候都不能选同一个条码当替代品。

- 替换划线的商品：划线的那一行**原地**改成新商品，去掉划线和灰字，批注「Sostituisce 旧条码」，不再往下挪。
- 选的新商品 Griglia 里已经有：Excel 不动，只写一条替换记录。

## 检查清单自动换成替代商品、门店报告（2026-11-11）

用户：订 DETERSIVI 之前先找好替代商品；门店清单里还写着停售的旧条码 → 自动换成替代商品去采购，门店报告里写明。

- `griglia_replace.substitutes(替换记录, Griglia)`：旧条码 → 新商品（A → B → C 跟到最后；新商品要在现在这份 Griglia 里）。
- `commands.run_check` 读完 Griglia 后 `apply_substitutes`：条码 / 名称换成新商品，原来的记在 `x["repl"]`；
  销量、库存、建议箱数照旧用旧商品的，箱规 / 件价按新条码的报价；主订单写新条码和新商品的编号（`write_checked_order`）。
  检查窗口「情况」列写「停售 · 已换成替代商品」（蓝字，鼠标提示写原商品 → 替代成）。
- 门店报告（`check_rules.write_problem_report`）：
  - ① Articoli Griglia non disponibili：换了的写原商品，右边替代商品和左边一一对应：Foto · EAN · Cod. Art. · Descrizione alternativo · Grossista · Colli ordinati；
    没找到替代商品的右边六格红底，排在后面。
  - ② 标题「Articoli non in Griglia – si consiglia di non ordinarli, salvo urgenze; altrimenti ordinarli il più possibile dai
    fornitori diretti」，最后加一列 NOTA（空着，浅黄，人工写）。
  - ② 右边（N–S 列）「Ordinabili dai fornitori diretti」：品牌供应商（直厂）SCHEDA 里有的商品按供应商汇总 SKU · Colli（清单要的箱数）·
    Importo（箱数 × SCHEDA 每箱件数 × SCHEDA 件价）· Minimo d'ordine（SCHEDA 起订量，手动改过的用改过的）· Minimo raggiunto
    （Sì 绿 / No – mancano … 红 / 按板没填每板箱数 Da verificare 黄）+ 合计（`direct_summary`）。
  - `store_orders.scheda_index` 缓存多记每箱件数 / 件价 / 起订量（老缓存自动重读一次），`brand_item`。

