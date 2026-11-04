"""개인 시놀로지에서 실행할 중앙 데이터 서버."""

__all__ = ["CentralServerSettings"]


def __getattr__(name):
    if name == "CentralServerSettings":
        from .config import CentralServerSettings
        return CentralServerSettings
    raise AttributeError(name)
