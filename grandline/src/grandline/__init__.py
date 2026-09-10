"""
WHAT THIS FILE DOES
Marks `grandline` as a Python package -- the presence of this file is what
lets `import grandline` work at all. It also holds the version number.

WHY IT'S NEARLY EMPTY
Top-level package files should stay thin. Putting logic here means it runs
on every single import of any submodule, which makes startup slow and
import errors confusing. Real code lives in the subpackages.

WHERE THE PROJECT IS
Stage 1 of 6: only `grandline.stats` exists. There is no database, no data
source, and no website yet. Coming next: `grandline.sources` (stage 2),
`grandline.pipeline` (stage 3), `grandline.web` (stage 4).
"""

# Single source of truth for the version. pyproject.toml carries its own
# copy for packaging; keep the two in step when you bump it.
__version__ = "0.1.0"
