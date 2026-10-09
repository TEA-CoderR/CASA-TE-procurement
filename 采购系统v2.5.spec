# -*- mode: python ; coding: utf-8 -*-
# 打包：python -m PyInstaller --noconfirm 采购系统v2.5.spec   （或双击 build.bat）

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'openpyxl', 'openpyxl.workbook', 'openpyxl.worksheet',
        'win32com', 'win32com.client',
        'PySide6.QtSvg',
        'perf_monitor', 'shadow_test', 'excel_pool', 'supplier_cache',
        'ai_matcher', 'learning_router', 'optimized_search',
        'min_order_optimizer', 'allocation_plan', 'settings', 'i18n', 'translations',
        'numpy', 'scipy.optimize', 'scipy.sparse',
        'ddgs', 'requests',
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='采购系统v2.5',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)
