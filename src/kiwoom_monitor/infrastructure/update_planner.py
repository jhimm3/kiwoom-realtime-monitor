"""GitHub 릴리즈 목록에서 안전한 업데이트 경로를 계산한다."""

from __future__ import annotations

from dataclasses import dataclass


def version_tuple(value: str) -> tuple[int, ...]:
    """숫자로만 이루어진 앱 버전을 비교 가능한 튜플로 바꾼다."""
    try:
        result = tuple(int(part) for part in value.strip().lstrip("vV").split("."))
    except ValueError:
        return ()
    return result if result and all(part >= 0 for part in result) else ()


@dataclass(frozen=True)
class UpdateStep:
    version: str
    update_url: str
    checksum_url: str
    size: int


@dataclass(frozen=True)
class UpdatePlan:
    latest_version: str
    release_url: str
    steps: tuple[UpdateStep, ...]
    update_size: int
    setup_url: str
    setup_size: int

    @property
    def should_use_setup(self) -> bool:
        return bool(self.setup_url and self.setup_size > 0 and self.update_size >= self.setup_size)

    @property
    def can_apply_steps(self) -> bool:
        return bool(self.steps) and all(step.update_url and step.checksum_url for step in self.steps)


def _asset(document: dict[str, object], name: str) -> tuple[str, int]:
    for raw in document.get("assets", ()) if isinstance(document.get("assets"), list) else ():
        if not isinstance(raw, dict) or str(raw.get("name", "")) != name:
            continue
        url = str(raw.get("browser_download_url", "")).strip()
        size = raw.get("size", 0)
        return url, size if isinstance(size, int) and size >= 0 else 0
    return "", 0


def build_update_plan(current_version: str, releases: list[dict[str, object]]) -> UpdatePlan | None:
    """현재 버전 이후의 공개 릴리즈를 오래된 순서로 묶는다.

    각 릴리즈의 부분 업데이트 ZIP은 바로 앞 공개 릴리즈 기준으로 만들어진다는
    배포 규칙에 따라, 중간 릴리즈를 빠뜨리지 않고 모두 포함한다.
    """
    current = version_tuple(current_version)
    if not current:
        return None
    published: list[tuple[tuple[int, ...], str, dict[str, object]]] = []
    for document in releases:
        if document.get("draft") is True or document.get("prerelease") is True:
            continue
        version = str(document.get("tag_name", "")).strip().lstrip("vV")
        parsed = version_tuple(version)
        if parsed and parsed > current:
            published.append((parsed, version, document))
    if not published:
        return None
    published.sort(key=lambda item: item[0])
    latest_version = published[-1][1]
    latest_document = published[-1][2]
    setup_url, setup_size = _asset(latest_document, f"KiwoomMonitor-Setup-{latest_version}.exe")
    steps: list[UpdateStep] = []
    for _, version, document in published:
        update_name = f"KiwoomMonitor-Update-{version}.zip"
        update_url, size = _asset(document, update_name)
        checksum_url, _ = _asset(document, f"{update_name}.sha256")
        steps.append(UpdateStep(version, update_url, checksum_url, size))
    return UpdatePlan(
        latest_version=latest_version,
        release_url=str(latest_document.get("html_url", "")).strip(),
        steps=tuple(steps),
        update_size=sum(step.size for step in steps),
        setup_url=setup_url,
        setup_size=setup_size,
    )
