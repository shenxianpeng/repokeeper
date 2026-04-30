#!/usr/bin/env python3
"""
RepoKeeper Agent — GitHub Actions entry point.
Thin wrapper that imports and runs the implementation agent from src.
"""

import os
import sys

# Add src to path so we can import repokeeper
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from repokeeper.agent import run_agent

if __name__ == "__main__":
    result = run_agent()
    if result.get("skip") and result.get("reason"):
        print(f"[repokeeper] Skipped: {result['reason']}")
    elif result.get("pr_url"):
        print(f"[repokeeper] PR created: {result['pr_url']}")
