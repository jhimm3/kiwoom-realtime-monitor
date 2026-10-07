import time

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()
    assert predicate(), "settings worker did not complete"


def dispose_dialogs():
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QDialog):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()
