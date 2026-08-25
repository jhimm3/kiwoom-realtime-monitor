"""수정된 개발 소스를 기존 개인 데이터와 연결해 테스트 실행한다."""

from __future__ import annotations

import argparse
from pathlib import Path

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
