# 给 AI 助手（Claude Code 等）的项目说明

这个项目之前主要是和 Claude 一起开发的。接手的人继续用 AI 助手时，助手会自动读这个文件。

- **先读 `交接文档.md`**（代码地图、业务规则、发布流程、安全），业务细节在 `docs/`。
- 用户是中文使用者：回复用中文，用业务语言解释，少用技术术语。
- 界面文字一律 `tr("中文原文")`，新增文字在 `translations.py` 末尾补英 / 意翻译；改完运行 `python tools/check_translations.py`。
- **界面要简洁（用户明确要求，2026-10-22）**：设置窗口、对话框只放短标签（几个字）+ 输入框 / 勾选框；说明文字一律放鼠标提示（tooltip，标题后加 ⓘ），不要在界面上放一段段说明。新加界面先想能不能少一个字、少一个按钮。
- 颜色用 `theme.T("token")`，不要写死；页面提供 `restyle()`。耗时操作用 `erp_widgets.TaskDialog` + 线程 + Signal（参考 `import_page.py` 的 `start()`）。
- 业务逻辑写成不依赖界面的函数并加测试；改动后跑 `python -m pytest -q test_*.py`（应全部通过）。
- `store_orders.plan_supplier` 同时决定起订量检查表的颜色和实际配货结果，改它就是改业务规则，先和用户确认。
- 新增 .py 模块要加进 `采购系统v2.5.spec` 的 `hiddenimports`。
- 保持文件原来的换行符（.py 是 LF）；Python 写文件用 `newline=""` 或二进制方式。
- 绝对不要提交密码、令牌、`settings.json`、`publish_config.json`、`update_config.json`、真实业务 Excel。数据库只读，程序不写库。
- 发布新版本用 `publish_update.bat`（会检查 git、改版本号、打包、提交、推送、建 Release），不要手动改 `theme.APP_VERSION` 后单独发布。
- 表格里可以直接编辑的格子（QStyledItemDelegate）：`setModelData` 里不要马上重画这一行（`table_set_row` 会换掉正在编辑的格子 → Qt 回头读新格子拿不到行号 → 程序卡住闪退）。先取出值，用 `QTimer.singleShot(0, ...)` 等编辑框关掉再更新；`setEditorData` 拿到的行号是 None 时直接 return。参考 `main.py` 的 `QtyDelegate` / `SupplierComboDelegate`。
- 用户和同事**共用同一个文件夹**（`procurement_system ok 2.5`）开发。请直接在这个文件夹里改代码、`git commit`、`git push`，不要只推到云端分支却把文件另外写进文件夹（之前这样做过，文件夹和 git 记录对不上，发布前得先同步）。

## 最近改动（给接手的 AI 的纸条，新的写在最上面）
- 2026-10-22：起订量检查加「BMS 调整」（全不选右边；点表格 BMS 格也行）→ `BmsAdjustDialog`：配货前选一家或全部勾选的供应商，勾哪几家门店哪几家就整单进 BMS，可「恢复系统建议」。存在 `StoreOrderPage.bms_picks`（只在这次运行里），`_preview` 和 `S.allocate(picks=)` 都按 mode "manual" 配；BMS 格带 ✎。
- 2026-10-22：配货结果**一家供应商一个订单文件** `Ordine_<供应商>_<ddmm>.xlsx`（`store_orders.write_plans`）：直送门店 + BMS 门店写在同一个文件，汇总页 GRIGLIA 门店名上一行写 DIRETTO / BMS（`_mark_griglia`、`plan_marks`）；`written` 仍是每个去向一行（路径相同），数文件用 `written_files`。新算法也能「按配置文件周期」：`compute_suggestions` 把每行供应商周期（没设的用算法设置的订货周期）传给 `apply_v2(cycles=)`。
- 2026-10-22（用户定的业务规则，第二版，先从简单开始）：**不借货、不把能直送的店拉进 BMS**。`plan_supplier` auto：够起订量（含小差额凑单）→ 直送；订不起来的按 `fill["bms_order"]`（默认 3-4-5-2-1，只算参与 BMS 凑单的门店 = 不在 far_stores / 供应商设置 bms_stores 里）一家家并进 BMS，够起订量就停；剩下的 / 凑不够的 → status "wait"（这次不订）。配货结果「手动选进 BMS 的门店…」（`BmsPickDialog` → mode "manual" + picks）可以把任意门店整单并进 BMS、可强制下单。配货结果加「紧急缺货」列（`store_orders.urgent_share / plan_urgent`：库存够卖 < 7 天的商品金额占比，红 ≥ 50%、黄 ≥ 25%）；状态改成格子里的彩色字。配货设置 ② 改成可勾选 + 拖动排序的门店列表。供应商设置可单独选参与 BMS / 调货的门店（store_orders.supplier_cfg）。
- 2026-10-21：`COMPANY_DEFAULTS_VER` = 2：按采购部电脑现在的设置统一一次（新算法参数、凑单 / 借货 / 调货、起订规则、报价文件名称、订货明细和检查清单的列顺序 / 显示哪些列 / 列宽）。检查清单默认列顺序 = 用户排好的（`order_check._ORDER`）。
- 2026-10-21：检查清单：数据库没有的商品也按条码去供应商报价里补箱规 / 件价（`price_check.apply_quotes`）；还是找不到的在工具条「全部商品」右边两个按钮「⚠ 数据库没有 / ⚠ 没有箱规」，点开是列表（`_problem` → nodb / nopack，`_fill_prob`、`_open_prob`），主表格只放能算的；调货列没调的一律写「不调」（订货明细也是）。界面卡住时 `logs/hang.log` 会记下各线程的调用栈（`main._start_hang_watchdog`）。
- 2026-10-21：检查门店订货清单改成**按箱**审核：`order_check.init_boxes` 给每行 lbox（清单箱数）/ sbox（建议箱数）/ fbox（确定箱数 ✎，`_set_box` 同步 final = 箱数 × 每箱件数）；判断按箱比；调货用 `_need_pcs`（箱数没改用系统件数）。确认生成的主订单不写 PEZZI（`write_main_order(pieces=False)`），比价按 COLLI 分配供应商。数据库没有 / 没箱规的商品在第二个标签页。负库存：显示原值、计算当 0、不提醒。
- 2026-10-21：检查门店订货清单窗口改成和订货明细一个样子：顶部一行工具条（门店按钮 `_build_chips`、搜索、「看哪些」下拉带个数、恢复建议值、更多 ▾），底部状态栏（显示几个 · 改了几个 · 估算金额 · 调货 + 「确认，开始比价」）。默认列顺序 / 藏哪些列改了（`_LAYOUT_VER` 6、`_HIDDEN_VER` 5，`settings.COMPANY_DEFAULTS` 的 check_cols_hidden 同步）。
- 2026-10-21：调货改成「系统建议一次 + 人工改」：双击「调货」格弹 `erp_widgets.TransferEditDialog`（选调出门店 + 件数 / 不调），不再用格子里的编辑框。第一次人工改（检查清单里改确定件数也算）就把系统建议冻结（`transfers.freeze`，overrides 里 auto=True），之后只按 overrides 调，改一行别的行不变；调货设置改了 / 恢复建议值才解冻（`unfreeze`）。系统建议改成每个商品从多出来最多的门店调（`transfers.plan`）。门店配货 `_preview` 加了缓存，改一个调货只重算那家供应商。
- 2026-10-21：配单订单文件：Colli 列只有订了的行留黄底（不订的行借用左边 PZ*COLLI 那一格的格式，`_set_cells(plain_col=)`）；月均 / 采购量 / 箱数 / 需求不写进文件（`store_orders.file_extra`，程序内部的 `extra_fields` 不变）。Codice Fornitore：SCHEDA 读 `sup_code`（订货明细一列）；批发商比价检查清单时从数据库查 `ArticoloForn` + 供应商名（`reorder_service.supplier_info`），存进快照，缺失商品列表 / 缺失报告显示；旧主订单在比价时按条码补查（`service.fill_missing_suppliers`）。
- 2026-10-20：公司统一默认设置写在 `settings.COMPANY_DEFAULTS`：每台电脑新装 / 更新后第一次打开时套用一次（`company_defaults_ver`），只改业务规则，不动个人设置。要再推一次新的统一设置：改值并把 `COMPANY_DEFAULTS_VER` 加 1。
- 2026-10-20：批发商比价改成「工作文件夹 + 一个开始按钮」：固定根目录（settings `wholesale_root`）下自动建 `1 门店清单 / 2 供应商报价 / 3 扣减文件\门店 / 4 比价结果\日期\门店`（`workspace.py`）。三个拖放区放文件；「开始」= 检查全部清单 → 检查窗口点「确认，开始比价」→ `MainWindow.run_batch` 一家一家自动比价（方案按推荐选，不弹确认）。开关、起订规则、检查参数收进「设置」窗口（`open_cmp_settings` 借用 `cmp_opts_box` / `order_check.params_box`，关掉还回去）。`folder_edit` 隐藏，只表示当前门店的比价文件夹。**还没在 Windows 上实际打开过，接手先打开一次。**
- 2026-10-19：侧边栏「采购管理」只留门店配货 / 批发商比价 / 采购订单跟踪，比价结果、历史记录、起订规则、缺失查找变成批发商比价顶部的标签页（`MainWindow.SUB_PAGES`）。第一版启动就闪退：在 class 里写的列表推导式用到了别的 class 变量（Python 里看不到，NameError），已改成模块级函数 `_all_pages`。**改 `MainWindow` 的 class 变量后，一定要在 Windows 上实际打开一次程序。**
- 2026-10-19：发布 v3.0.2（第一次带补丁包的自动更新）。发布脚本改了：出错先显示原因；git 没设名字 / 邮箱时一开始就问。这个文件夹的 git 提交人已设为 TEA-CoderR。
- 2026-10-19（515ac81）：修复「修改分配」窗口改箱数 / 换供应商后程序卡住闪退（原因见上面表格编辑那一条）。只改了 `main.py` 里两个 delegate，业务逻辑没动；103 个测试通过。写回主订单和供应商文件那一步还等用户用真实文件确认。
