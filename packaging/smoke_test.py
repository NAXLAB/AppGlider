"""Open the packaged GUI with a disposable first-run home, without FUSE."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    image = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="appharbour first run ") as temporary:
        home = Path(temporary)
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("PYTHON", "QT_", "XDG_"))
            and key
            not in {"APPDIR", "APPIMAGE", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        }
        environment.update(
            HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            XDG_CACHE_HOME=str(home / ".cache"),
            QT_QPA_PLATFORM="offscreen",
        )
        result = subprocess.run(
            [str(image), "--appimage-extract-and-run", "--smoke-test"],
            env=environment,
            cwd=home,
            text=True,
            capture_output=True,
            timeout=90,
        )
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        report = next(
            json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
        )
        assert report["app"] == "AppHarbour" and report["icon"], report
        assert report["https_certificates"] > 0, report
        assert report["installed"] == 0 and report["catalog"] == 14, report
        assert report["python"].startswith("3.13."), report
        assert Path(report["prefix"]).is_relative_to(report["appdir"]), report
        assert report["command"] == [str(image)], report
        assert Path(report["data"]).is_relative_to(home), report
        shortcut = home / ".local/share/applications/appharbour.desktop"
        assert str(image) in shortcut.read_text(), shortcut.read_text()
        assert report["appdir"] not in shortcut.read_text(), shortcut.read_text()
        print(json.dumps(report, indent=2))
        print("Packaged first-run GUI and stable menu shortcut passed.")


if __name__ == "__main__":
    main()
