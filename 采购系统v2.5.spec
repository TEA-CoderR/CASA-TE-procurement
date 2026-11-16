# -*- mode: python ; coding: utf-8 -*-
# 打包：双击 build.bat（会自动保留 settings.json 和历史记录）
# 输出：dist\Procurement\CASA&TE采购系统.exe（整个 Procurement 文件夹一起用，不能只拷 exe）

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('fonts/Inter-*.ttf', 'fonts'), ('fonts/JetBrainsMono-*.ttf', 'fonts'), ('fonts/*OFL*.txt', 'fonts')],   # Noto Sans SC 不打包（Windows 用雅黑）
    hiddenimports=[
        'openpyxl', 'openpyxl.workbook', 'openpyxl.worksheet',
        'win32com', 'win32com.client',
        'PySide6.QtSvg',
        'perf_monitor', 'shadow_test', 'excel_pool', 'supplier_cache',
        'ai_matcher', 'learning_router', 'optimized_search',
        'min_order_optimizer', 'allocation_plan', 'file_inbox', 'scenarios', 'originals', 'supplier_names', 'transfers', 'price_check', 'workspace', 'settings', 'i18n', 'translations', 'history', 'supplier_layout',
        'reorder_service', 'reorder_page', 'store_orders', 'store_order_page', 'order_check', 'forecast', 'theme', 'erp_widgets', 'product_images', 'griglia', 'griglia_page', 'griglia_replace', 'griglia_replace_dialog', 'product_window', 'order_tracking', 'order_record', 'excel_sync', 'commands', 'agent', 'agent_tools', 'agent_panel', 'review_memory', 'volantino', 'volantino_page', 'sellout', 'sellout_page', 'page_kit', 'volantino_report', 'check_rules', 'credentials', 'mail_client', 'mail_match', 'mail_track_dialog', 'mail_prices', 'mail_price_dialog', 'mail_page', 'xlsx_report', 'item_drawer', 'tracking_board', 'command_palette', 'mail_send', 'send_order_dialog', 'order_mail_tab', 'tracking_page', 'deter_arrival', 'transit_dialog', 'company_defaults', 'company_sync', 'dashboard_page', 'settings_page', 'legacy_pages', 'store_detail_window', 'distribution_service', 'distribution_page', 'import_page', 'app_update', 'update_ui', 'secure_store', 'fenpei', 'fenpei.config', 'fenpei.database', 'fenpei.models',
        'pandas', 'sqlalchemy', 'sqlalchemy.dialects.mysql', 'sqlalchemy.dialects.mysql.mysqldb', 'MySQLdb', 'pymysql',
        'numpy', 'scipy.optimize', 'scipy.sparse',
        'ddgs', 'requests',
        'PIL', 'PIL.Image', 'PIL.WebPImagePlugin',     # 商品图片是 webp：Qt 读不了时用 Pillow 转
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 用不到的大模块不打包，减小体积
    excludes=[
        'tkinter', 'sv_ttk', 'matplotlib', 'IPython', 'pytest',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
        'PySide6.QtQuick', 'PySide6.QtQml', 'PySide6.QtQuickWidgets', 'PySide6.Qt3DCore',
        'PySide6.QtMultimedia', 'PySide6.QtCharts', 'PySide6.QtDataVisualization',
        'PySide6.QtPdf', 'PySide6.QtPdfWidgets', 'PySide6.QtBluetooth', 'PySide6.QtPositioning',
        'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtDesigner', 'PySide6.QtNetwork',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# 文件夹模式（onedir）：exe 和库文件放在 dist\Procurement\ 里，启动时不再解压到 Temp，
# 关闭时也就不会出现「Failed to remove temporary directory」，Temp 里不会留下 _MEI 文件夹。
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CASA&TE采购系统',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Procurement',
)
