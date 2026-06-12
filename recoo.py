#!/usr/bin/env python3
"""recoo entry point.

Run directly:  ./recoo.py -d example.com
Or installed:  recoo -d example.com
"""
import sys

from recoo.cli import main

if __name__ == "__main__":
    sys.exit(main())
