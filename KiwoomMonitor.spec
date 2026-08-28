# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_dynamic_libs
from pathlib import Path
import os
import shutil
import stat

datas = [('data/near_high_sounds', 'data/near_high_sounds'), ('data/ocr_models/PP-OCRv5_mobile_det', 'data/ocr_models/PP-OCRv5_mobile_det'), ('data/ocr_models/korean_PP-OCRv5_mobile_rec', 'data/ocr_models/korean_PP-OCRv5_mobile_rec'), ('resources/app_icon.png', 'resources'), ('resources/UpdateHelper.exe', 'update_helper'), ('THIRD_PARTY_LICENSES.txt', '.'), ('licenses', 'licenses')]
binaries = []
datas += collect_data_files('paddle')
binaries += collect_dynamic_libs('paddle')
# PyInstaller 6.22/PySide 6.11 조합은 Qt 핵심 DLL을 PySide6 하위에만
# 배치할 수 있다. Windows 로더는 QtGui.pyd를 불러올 때 _internal 루트도
# 기준으로 삼으므로, 정상 동작한 이전 배포본과 같이 최상위 DLL을 루트에
# 한 벌 더 둔다. 플랫폼 플러그인 등 하위 폴더 구조는 기존 hook이 유지한다.
vc_runtime_names = {
    'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll',
    'icuuc.dll',
}
# PySide 6.11 wheel에 동봉된 VC 14.44 런타임은 현재 Qt6Core가 요구하는
# 함수를 제공하지 못한다. Windows에 설치된 최신 재배포 런타임을 앱에
# 포함해 다른 PC에서도 동일한 파일 집합으로 실행되게 한다.
system32 = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'System32'
binaries += [(str(system32 / name), '.') for name in vc_runtime_names if (system32 / name).exists()]


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
    excludes=[
        'paddle.jit.sot',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
    ],
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

# hook/Analysis가 같은 이름의 구형 VC 런타임을 뒤늦게 선택하더라도 최종
# 배포 폴더에는 System32의 최신 재배포 파일이 남도록 마지막에 교체한다.
runtime_output = Path(DISTPATH) / 'KiwoomMonitor' / '_internal'
for library_folder in ('PySide6', 'shiboken6'):
    collected_folder = runtime_output / library_folder
    if collected_folder.exists():
        for runtime_source in collected_folder.glob('*.dll'):
            if runtime_source.name.casefold() not in vc_runtime_names:
                shutil.copy2(runtime_source, runtime_output / runtime_source.name)
for runtime_name in vc_runtime_names:
    runtime_source = system32 / runtime_name
    if runtime_source.exists() and runtime_output.exists():
        runtime_destination = runtime_output / runtime_name
        shutil.copy2(runtime_source, runtime_destination)
        runtime_destination.chmod(stat.S_IREAD | stat.S_IWRITE)
