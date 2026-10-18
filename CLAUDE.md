# 给 AI 助手（Claude Code 等）的项目说明

这个项目之前主要是和 Claude 一起开发的。接手的人继续用 AI 助手时，助手会自动读这个文件。

- **先读 `交接文档.md`**（代码地图、业务规则、发布流程、安全），业务细节在 `docs/`。
- 用户是中文使用者：回复用中文，用业务语言解释，少用技术术语。
- 界面文字一律 `tr("中文原文")`，新增文字在 `translations.py` 末尾补英 / 意翻译；改完运行 `python tools/check_translations.py`。
- 颜色用 `theme.T("token")`，不要写死；页面提供 `restyle()`。耗时操作用 `erp_widgets.TaskDialog` + 线程 + Signal（参考 `import_page.py` 的 `start()`）。
- 业务逻辑写成不依赖界面的函数并加测试；改动后跑 `python -m pytest -q test_*.py`（应全部通过）。
- `store_orders.plan_supplier` 同时决定起订量检查表的颜色和实际配货结果，改它就是改业务规则，先和用户确认。
- 新增 .py 模块要加进 `采购系统v2.5.spec` 的 `hiddenimports`。
- 保持文件原来的换行符（.py 是 LF）；Python 写文件用 `newline=""` 或二进制方式。
- 绝对不要提交密码、令牌、`settings.json`、`publish_config.json`、`update_config.json`、真实业务 Excel。数据库只读，程序不写库。
- 发布新版本用 `publish_update.bat`（会检查 git、改版本号、打包、提交、推送、建 Release），不要手动改 `theme.APP_VERSION` 后单独发布。
