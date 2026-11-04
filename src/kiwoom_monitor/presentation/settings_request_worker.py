"""설정창의 단건 I/O 실행. 창이 닫혀도 작업을 강제 파괴하지 않는다."""

from __future__ import annotations

import atexit
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot


_running_requests: set["SettingsRequestWorker"] = set()


class SettingsRequestWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self, task: Callable[[], object], parent: QObject | None = None, *,
        retain_until_joined: bool = False,
    ) -> None:
        super().__init__(parent)
        self._task = task
        self._retain_until_joined = retain_until_joined
        self.finished.connect(self._release)

    def start(self) -> None:
        _running_requests.add(self)
        super().start()

    def run(self) -> None:
        try:
            result = self._task()
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)
        finally:
            self._task = lambda: None

    @Slot()
    def _release(self) -> None:
        if self._retain_until_joined:
            return
        _running_requests.discard(self)
        self.deleteLater()

    def release_after_join(self, *, wait: bool = False) -> bool:
        """Release an explicitly retained request only after actual native join.

        A false result keeps this object's owner and Qt lifetime intact. Owners
        also retain the task result before calling this nonblocking release.
        An explicit finished-owner callback may request the same exact blocking
        native join. The default and the ordinary finished slot stay unchanged.
        """
        joined = self.wait() if wait else self.wait(0)
        if not joined:
            return False
        _running_requests.discard(self)
        self.deleteLater()
        return True


@atexit.register
def _drain_settings_requests() -> None:
    # HTTP calls have their own bounded timeout. wait joins the real thread,
    # rather than claiming requestInterruption stopped a synchronous call.
    for worker in tuple(_running_requests):
        worker.wait()
