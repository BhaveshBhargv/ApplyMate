"""Test setup: run from the repo root so pages/ and imports resolve, and make the tests/ helpers importable.

The suite never touches real services: fixtures replace Supabase with an in-memory fake (fake_supabase.py)
and ignore .streamlit/secrets.toml (which may hold real keys). Run with:  python -m pytest -q tests
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(ROOT)
