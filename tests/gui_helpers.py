"""Dispose test windows safely, including after assertions leave a task unfinished."""

from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtWidgets import QMessageBox


def close_window(window):
    window.quitting = True
    window.close_after_task = True
    window.close_after_release = True
    window.release_queue.clear()
    for task in (window.task, window.release_task):
        if task is not None:
            task.cancel.set()
    with patch(
        "appharbour.window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes
    ):
        if not QThreadPool.globalInstance().waitForDone(5000):
            raise AssertionError("Background worker did not stop during GUI test cleanup")
        QCoreApplication.processEvents()
        # Mocked workers never emit completion signals; real workers have stopped above.
        window.task = None
        window.release_task = None
        window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
