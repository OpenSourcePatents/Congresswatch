#!/usr/bin/env python3
"""
bootstrap.py — CongressWatch repo health check and directory setup.
Creates required data directories, checks data state, prints summary.
"""

import os
import json
import sys


REQUIRED_DIRS = [
    "data",
    "data/details",
    "data/bills",
    "data/cache",
]

MEMBERS_FILE = "data/members.json"
BILLS_CACHE = "data/bills/all_bills.json"
DETAILS_DIR = "data/details"


def main():
    print("CongressWatch — Bootstrap")
    print("=" * 40)

    # 1. Create required directories
    for d in REQUIRED_DIRS:
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            print(f"  Created: {d}/")
        else:
            print(f"  OK:      {d}/")

    # 2. Check members.json
    if not os.path.exists(MEMBERS_FILE):
        print(f"\n  [!] {MEMBERS_FILE} not found.")
        print("      Run the fetch pipeline first:")
        print("        python fetch.py")
        print("      Or trigger the 'Fetch Congress Data' GitHub Action.")
        sys.exit(1)

    try:
        with open(MEMBERS_FILE, "r") as f:
            members = json.load(f)
        member_count = len(members)
    except Exception as e:
        print(f"\n  [!] Could not read {MEMBERS_FILE}: {e}")
        sys.exit(1)

    # 3. Count detail files
    detail_count = 0
    if os.path.exists(DETAILS_DIR):
        detail_count = len([
            f for f in os.listdir(DETAILS_DIR)
            if f.endswith(".json")
        ])

    # 4. Bills cache size
    bills_size = "not found"
    if os.path.exists(BILLS_CACHE):
        size_bytes = os.path.getsize(BILLS_CACHE)
        bills_size = f"{size_bytes / 1024 / 1024:.1f} MB"

    # 5. Print summary
    print(f"\n  Data Summary:")
    print(f"    Members:       {member_count}")
    print(f"    Detail files:  {detail_count}")
    print(f"    Bills cache:   {bills_size}")

    scored = sum(1 for m in members if (m.get("score") or 0) > 0)
    print(f"    Members scored: {scored} / {member_count}")

    print("\n  Bootstrap complete.")


if __name__ == "__main__":
    main()
