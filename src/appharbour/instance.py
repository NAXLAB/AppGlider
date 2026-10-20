"""Keep one AppHarbour running per data directory and let a second launch wake it."""

import hashlib
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

CONNECT_TIMEOUT_MS = 1000


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self.name = "appharbour-" + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
        self._lock = QLockFile(str(directory / "appharbour.lock"))
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        """Return True when this process is the only instance and now owns the lock."""
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self._lock.tryLock(0):
            self._wake_existing()
            return False
        QLocalServer.removeServer(self.name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if server.listen(self.name):
            server.newConnection.connect(self._accept)
            self._server = server
        return True

    def release(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        self._lock.unlock()

    def _wake_existing(self) -> None:
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if socket.waitForConnected(CONNECT_TIMEOUT_MS):
            socket.write(b"show")
            socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
            socket.disconnectFromServer()

    def _accept(self) -> None:
        while self._server is not None and (socket := self._server.nextPendingConnection()):
            socket.disconnected.connect(socket.deleteLater)
            self.activated.emit()
