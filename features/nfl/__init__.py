"""Shared NFL feature definitions. Pure functions, no database and no I/O."""

from features.nfl.defs import CONTRACT, build_features

__all__ = ["CONTRACT", "build_features"]
