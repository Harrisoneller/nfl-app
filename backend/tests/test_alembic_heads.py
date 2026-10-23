"""The Alembic script directory must have exactly one head.

Two heads means two branches revised the same parent and `upgrade head`
is ambiguous. The 0012/0013 fork was joined in 0015_admin_overrides;
this test keeps that from happening again.
"""
from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_BACKEND = Path(__file__).resolve().parents[1]


def test_alembic_script_directory_has_exactly_one_head():
    config = Config(str(_BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(_BACKEND / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"expected one alembic head, found {heads}"
