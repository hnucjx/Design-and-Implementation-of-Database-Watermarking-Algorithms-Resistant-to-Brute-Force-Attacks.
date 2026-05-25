import os
import subprocess
import sys
from pathlib import Path


def open_path_with_default_app(path: Path) -> None:
    target = str(path)
    if os.name == "nt":
        os.startfile(target)  # type: ignore[attr-defined]
        return
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
