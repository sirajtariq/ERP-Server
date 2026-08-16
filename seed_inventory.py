"""
Standalone script to populate mock inventory items and stock movements.

Usage:
    uv run python seed_inventory.py
"""

import os
import sys
import django

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "erp_backend.settings")
django.setup()

from django.core.management import call_command

if __name__ == "__main__":
    print("Executing inventory seed command...")
    call_command("seed_inventory_data")
