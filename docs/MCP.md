# 采购系统的 MCP 服务（2026-11-26）

让 Claude（桌面版 / 连到这台电脑的云端会话）直接查程序里的数据，不用截图、不用传文件。

## 安装（每台电脑一次）
**推荐（2026-11-26 在 XH 电脑上试过能用）**：Claude 设置 → Desktop app → Extensions，把 `CASATE采购系统.mcpb` 拖进去点安装。
mcpb 就是一个 zip：manifest.json（server.mcp_config 直接写 python.exe 和 mcp_server.py 的绝对路径）+ 占位的 server/launch.py；别的电脑路径不一样要改 manifest 重新打包。
改 claude_desktop_config.json 的老办法不稳：Claude 开着时写进去，退出时会被它自己的保存盖掉（要先退出 Claude 再双击 MCP_install.bat）。

老办法：
1. 双击程序文件夹里的 `MCP_install.bat`（= `python mcp_server.py --install`）：把这个服务写进 Claude 桌面版的设置
   `%APPDATA%\Claude\claude_desktop_config.json`（商店版在 `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`），
   别的 MCP 服务留着，改之前备份一份 `.bak_时间`。
2. 完全退出 Claude 桌面版（托盘图标 → 退出）再打开。设置 → 开发者里能看到 casate-procurement。

服务用这台电脑的 Python 从源代码跑（`mcp_server.py`），读的是程序文件夹里同一份 settings.json、日志、本机保存的密码（credentials，DPAPI）。

## 有哪些工具（第 1 期全部只读）
- `tracking_summary` / `order_get` / `orders_list`：采购订单跟踪统计、一张单的全部字段和修改记录、按条件查单
- `db_query`：只读 SQL（只许一条 SELECT / SHOW / DESCRIBE / EXPLAIN，自动加 LIMIT，最多 500 行；数据库账号本来也是只读的）
- `log_tail`：procurement / stall / hang / crash / audit 日志最后几行，可按文字筛
- `settings_view`：设置（密码 / 令牌 / 密钥去掉）
- `files_list` / `excel_peek`：只在程序文件夹、同步文件夹、订单跟踪 / DETERSIVO / 总仓 / 门店采购文件夹里看；settings.json 这类有密码的文件不给看
- 采购助手里不改数据的工具：overview、store_list_suppliers、store_compute、store_result、store_supplier_lines、store_set_boxes（只改这次计算的内存）、mail_check（邮箱只读）、order_mail_preview

## 第 2 期：能改订单跟踪（2026-11-26 用户：「可以添加改的权限，我不想动手」）
都是两步：`*_prepare` 只列出要改哪几张、改完会怎样，返回一个确认号 → Claude 给用户看，**用户在对话里说可以** → `apply_change(确认号)` 才改；
做完能 `undo_change(确认号)` 撤销（只限这次连接里做的）。确认号 30 分钟有效、只能用一次；执行时按当时的数据再核一遍（例如作废只作废那时还该作废的）。
- `drafts_void_prepare`：作废旧的待登记（规则和程序里一样，`OT.stale_drafts`）
- `drafts_restore_prepare`：作废的恢复成待登记
- `orders_update_prepare`：已下单 / 供应商已确认 / 卡住了 / 恢复正常 / 取消 / 只加备注
- `mail_apply_prepare`：按 mail_check 的建议改（先在同一次连接里 mail_check）

改的人记成「本机用户 · Claude」，写在订单跟踪文件夹里单独一个日志文件（log_<电脑>_<用户 · Claude>.jsonl），不和程序抢同一个文件；程序会自动读到，PIANO Excel 程序下次同步时写进去。每次都记审计（via = mcp）。
**不给**：确认收货 / 部分收货（一定要门店的人点、选卸货人）、发邮件、生成订单、改 Griglia / 门店清单。每次调用写一条审计（logs/audit.jsonl，via = mcp）。

## 代码
- `mcp_server.py`：JSON-RPC 2.0 一行一条（MCP stdio），initialize / tools/list / tools/call / ping；stdout 只写协议，别的 print 都改到 stderr。
- `mcp_tools.py`：工具（不依赖界面）。以后加工具：写一个 `fn(ctx, args)` → 放进 `build_tools()`；会改数据的先和用户商量怎么确认。
- 测试 test_mcp.py。
