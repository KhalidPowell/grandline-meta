"""
WHAT THIS FILE DOES
Marks `grandline` as a Python package -- the presence of this file is what
lets `import grandline` work at all. It also holds the version number.

WHY IT'S NEARLY EMPTY
Top-level package files should stay thin. Putting logic here means it runs
on every single import of any submodule, which makes startup slow and
import errors confusing. Real code lives in the subpackages.

WHERE THE PROJECT IS
Stage 4 of 6 (PRD v2.2): `grandline.stats` (the maths) and
`grandline.capture` (parser, SQLite store, live log watcher) exist. Coming
next: a local page showing the record (stage 5).
"""

# Single source of truth for the version. pyproject.toml carries its own
# copy for packaging; keep the two in step when you bump it.
__version__ = "0.1.0"
