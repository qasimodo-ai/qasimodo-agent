#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyinstaller>=6.16.0"]
# ///

import subprocess
import sys

result = subprocess.run([
    "pyinstaller",
    "src/pyinstaller_test/__main__.py",
    "--name", "pyinstaller-test",
    "--onefile"
])

sys.exit(result.returncode)
