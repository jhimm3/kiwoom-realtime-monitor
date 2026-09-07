"""수정된 개발 소스를 기존 개인 데이터와 연결해 테스트 실행한다."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 이 스크립트를 어느 가상환경에서 실행하더라도 현재 워크트리의 소스를
# 메인·뉴스·매매일지 자식 프로세스가 동일하게 사용하도록 한다.
SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
source_text = str(SOURCE_ROOT)
if source_text not in sys.path:
    sys.path.insert(0, source_text)
existing_pythonpath = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = source_text + (os.pathsep + existing_pythonpath if existing_pythonpath else "")

from kiwoom_monitor.infrastructure.app_paths import AppPaths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()

    def test_paths(cls: type[AppPaths]) -> AppPaths:
        log_dir = data_dir / "logs"
        data_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        return cls(data_dir, log_dir, data_dir / "monitor.sqlite3")

    AppPaths.for_current_user = classmethod(test_paths)
    from kiwoom_monitor.bootstrap import main as run_app

    run_app()


if __name__ == "__main__":
    main()
