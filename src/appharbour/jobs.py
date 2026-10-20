"""One cancellable background operation at a time; results return through Qt signals."""

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from appharbour.icons import IconCandidate
from appharbour.release_info import ReleaseInfo
from appharbour.releases import Cancelled, Progress, Release


@dataclass
class TaskResult:
    message: str
    releases: dict[str, Release] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    icons: tuple[IconCandidate, ...] = ()
    release_infos: dict[str, ReleaseInfo] = field(default_factory=dict)


class TaskSignals(QObject):
    progress = Signal(str, object, object)
    finished = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, operation: Callable[[Event, Progress], TaskResult]) -> None:
        super().__init__()
        self.operation = operation
        self.cancel = Event()
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            result = self.operation(self.cancel, self.signals.progress.emit)
        except Cancelled as error:
            self.signals.finished.emit(TaskResult(str(error)))
        except Exception as error:
            self.signals.failed.emit(str(error))
        else:
            self.signals.finished.emit(result)
