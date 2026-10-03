# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('D:/DSH/local-translate/web', 'web'), ('D:/DSH/local-translate/config.json', '.'), ('D:/DSH/local-translate/server.py', '.'), ('D:/DSH/local-translate/cli.py', '.'), ('D:/DSH/local-translate/video-to-subtitle.py', '.'), ('D:/DSH/local-translate/image-translate.py', '.'), ('D:/DSH/local-translate/live_captions.py', '.'), ('D:/DSH/local-translate/summarize.py', '.')]
binaries = []
hiddenimports = ['pystray._win32', 'webview.platforms.winforms']
hiddenimports += collect_submodules('soundcard')
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:/DSH/local-translate/app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'manga_ocr', 'transformers', 'matplotlib', 'scipy', 'pandas'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='本地翻译',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='本地翻译',
)
