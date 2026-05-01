#!/usr/bin/env python3
"""Main runner script - orchestrates all enabled strategies.

This script is designed to run via GitHub Actions on a 4H cron schedule.
It discovers all enabled strategies and runs them through the pipeline.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from core.runner import run_all_strategies


if __name__ == "__main__":
    run_all_strategies()
