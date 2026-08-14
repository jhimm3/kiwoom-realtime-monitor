#!/usr/bin/env bash
set -euo pipefail

# macOS에서 실행: bash macos/build_app.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

python3 -m venv .venv-macos
source .venv-macos/bin/activate
python -m pip install --upgrade pip
python -m pip install . pyinstaller

rm -rf build/macos dist/macos
python -m PyInstaller \
  --noconfirm --clean --windowed \
  --name "키움 실시간 모니터" \
  --paths src \
  --distpath dist/macos \
  --workpath build/macos \
  --specpath build/macos \
  src/kiwoom_monitor/__main__.py

mkdir -p dist/macos/dmg-root
cp -R "dist/macos/키움 실시간 모니터.app" dist/macos/dmg-root/
hdiutil create -volname "키움 실시간 모니터" -srcfolder dist/macos/dmg-root -ov -format UDZO "dist/macos/키움_실시간_모니터.dmg"
echo "완료: dist/macos/키움_실시간_모니터.dmg"
