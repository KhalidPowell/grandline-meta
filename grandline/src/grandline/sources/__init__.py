"""
WHAT THIS FILE DOES
Makes `grandline.sources` a package and re-exports its public names.

WHY IT EXISTS
Same front-door pattern as `grandline.stats`: callers write
`from grandline.sources import RawMatch`, and this list is the promise
of what's stable to import from outside the package.
"""

from grandline.sources.base import RawMatch

__all__ = [
    "RawMatch",
]
