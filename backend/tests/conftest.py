"""Shared test setup. Owned by the lead."""

import os

# Tests drive Playwright's bundled Chromium against local fixture pages, never the real Chrome app.
os.environ.setdefault("BROWSER", "chromium")
