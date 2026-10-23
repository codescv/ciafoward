#!/usr/bin/env python3
"""
Entry-point script for `ciafoward` / `gba2cia`.
Usage:
    ./gba2cia.py ~/Downloads/"Advance Wars (USA) (Rev 1).gba"
"""

import sys
from ciafoward.cli import main

if __name__ == "__main__":
    sys.exit(main())
