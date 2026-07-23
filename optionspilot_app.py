"""PyInstaller entry point for OptionsPilot.

Double-clicking the exe opens the desktop app; command-line arguments pass
through to the normal CLI (e.g. `OptionsPilot.exe scan`, `OptionsPilot.exe
serve --port 8787`).
"""

import multiprocessing
import os
import sys

from optionspilot.__main__ import main


def unblock_bundle() -> None:
    """Strip the Windows Mark-of-the-Web from the app's own files.

    A release zip downloaded from GitHub and extracted with Explorer leaves
    every extracted file carrying a Zone.Identifier "from the internet"
    stream. .NET Framework refuses to load managed assemblies flagged that
    way (NotSupportedException, HRESULT 0x80131515), so pywebview's WinForms
    backend dies inside pythonnet before the window opens:
    "Failed to resolve Python.Runtime.Loader.Initialize from
    Python.Runtime.dll". Deleting the stream from our own install folder is
    exactly what Explorer's "Unblock" checkbox does; it must happen before
    `import clr` (i.e. before webview.start()). Files without the stream and
    files we can't write to are skipped silently — a locally built bundle has
    no streams to remove, and on a read-only install the app behaves no worse
    than before.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    root = os.path.dirname(sys.executable)
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
            except OSError:
                pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unblock_bundle()
    args = sys.argv[1:] or ["ui"]
    sys.exit(main(args))
