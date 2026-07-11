"""PyInstaller entry point for OptionsPilot.

Double-clicking the exe opens the desktop app; command-line arguments pass
through to the normal CLI (e.g. `OptionsPilot.exe scan`, `OptionsPilot.exe
serve --port 8787`).
"""

import multiprocessing
import sys

from optionspilot.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    args = sys.argv[1:] or ["ui"]
    sys.exit(main(args))
