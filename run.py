#!/usr/bin/env python3
"""Convenience wrapper so `python run.py fetch` works from the project folder."""
from tender_radar.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
