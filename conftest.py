# conftest.py
import sys
import os
import pytest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, str(Path(__file__).parent / "server"))

# Shared SQLite schema for test DB fixtures — single source of truth.
_SCHEMA_DDL = """
    CREATE TABLE project (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, root_dir TEXT,
      exported_at TEXT NOT NULL, exported_by TEXT NOT NULL,
      schema_version INTEGER NOT NULL, sheet_count INTEGER NOT NULL,
      component_count INTEGER NOT NULL, net_count INTEGER NOT NULL
    );
    CREATE TABLE sheets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
    CREATE TABLE variants (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL,
      dnp_refdes TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE components (
      id INTEGER PRIMARY KEY, refdes TEXT NOT NULL UNIQUE,
      mpn TEXT, description TEXT, value TEXT,
      sheet_id INTEGER REFERENCES sheets(id)
    );
    CREATE TABLE nets (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
      pin_count INTEGER NOT NULL
    );
    CREATE TABLE pins (
      id INTEGER PRIMARY KEY, component_id INTEGER NOT NULL,
      pin_number TEXT NOT NULL, pin_name TEXT NOT NULL, net_name TEXT
    );
"""


@pytest.fixture(autouse=True)
def _reset_main_state():
    """Reset module-level globals in main between tests to prevent state leakage."""
    yield
    main = sys.modules.get("main")
    if main is not None:
        main._project = None
        main._sheets = []
        main._variants = []
        main._active_variant = None
        main._netlist = {}
