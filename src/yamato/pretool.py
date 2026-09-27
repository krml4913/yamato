"""The PreToolUse hook's first look (§0 B4).

It runs before every tool call of every seat, so the ``yamato`` entry calls it
before the CLI is imported: the standard library, one JSON file, and straight
back out while the ship is running. Past the deadline ``hooks.pre_tool_use``
takes over.
"""
from __future__ import annotations

import json
import sys
import time


def main(ship: str, seat: str) -> int:
    try:
        with open(f"{ship}/.runtime/deadline", encoding="utf-8") as f:
            if time.time() < json.load(f)["deadline"]:
                return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0   # not up: no limit to hold
    from pathlib import Path

    from . import hooks

    try:
        return hooks.pre_tool_use(Path(ship), seat)
    except Exception as e:  # a broken hook must never wedge the seat
        print(f"yamato hook pre-tool-use: {e}", file=sys.stderr)
        return 1
