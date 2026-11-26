# 采购系统的 MCP 服务（2026-11-26）

让 Claude（桌面版 / 连到这台电脑的云端会话）直接查程序里的数据，不用截图、不用传文件。

## 安装（每台电脑一次）
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

会改订单 / 共享数据 / 发邮件的工具**不给**：照旧在程序里点（程序会先弹确认、能撤销）。每次调用写一条审计（logs/audit.jsonl，via = mcp）。

## 代码
- `mcp_server.py`：JSON-RPC 2.0 一行一条（MCP stdio），initialize / tools/list / tools/call / ping；stdout 只写协议，别的 print 都改到 stderr。
- `mcp_tools.py`：工具（不依赖界面）。以后加工具：写一个 `fn(ctx, args)` → 放进 `build_tools()`；会改数据的先和用户商量怎么确认。
- 测试 test_mcp.py。
