"""Command entry: ``python -m research_desk <command>`` (spec §5), run from the repository folder.

The command's exit code becomes the process's exit code.
"""
import sys

from research_desk.cli import main

if __name__ == "__main__":
    sys.exit(main())
