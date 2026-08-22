# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs

datas = [('data/near_high_sounds', 'data/near_high_sounds'), ('data/ocr_models/PP-OCRv5_mobile_det', 'data/ocr_models/PP-OCRv5_mobile_det'), ('data/ocr_models/korean_PP-OCRv5_mobile_rec', 'data/ocr_models/korean_PP-OCRv5_mobile_rec'), ('resources/app_icon.png', 'resources')]
binaries = []
datas += collect_data_files('paddle')
binaries += collect_dynamic_libs('paddle')


a = Analysis(
    ['src/kiwoom_monitor/__main__.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=['paddleocr', 'paddlex', 'googleapiclient.discovery', 'google_auth_oauthlib.flow'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # OCR에 필요하지 않은 JIT 하위 모듈은 Python 3.13에서 패키지 분석 중
    # 별도 프로세스를 종료시키므로 배포 대상에서 제외한다.
    excludes=['paddle.jit.sot'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KiwoomMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='resources/app_icon.ico',
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
    name='KiwoomMonitor',
)
