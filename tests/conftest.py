"""Shared pytest fixtures — point the app at an in-memory SQLite for all tests."""

import os

# Set before app modules are imported by any test.
os.environ.setdefault("DB_URL", "sqlite:///:memory:")
os.environ.setdefault("MODE", "read-only")
