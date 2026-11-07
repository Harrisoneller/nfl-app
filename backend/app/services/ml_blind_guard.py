"""Refuse every NFL ML fit until an external QA SHA matches this tree.

The lock commit is identified by git. No SHA is embedded in the source.
Importing this module does not fit a model or read a season.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ml_blind_spec import spec_document

REPO_ROOT = Path(__file__).resolve().parents[3]
RESULTS_PATH = REPO_ROOT / "research" / "nfl-ml-blind" / "RESULTS.json"
SPEC_PATH = REPO_ROOT / "research" / "nfl-ml-blind" / "TRAIN_SPEC.json"
CODE_PATHS = (
    "research/nfl-ml-blind/PREREG.md",
    "research/nfl-ml-blind/TRAIN_SPEC.json",
    "backend/app/services/ml_blind_spec.py",
    "backend/app/services/ml_blind_features.py",
    "backend/app/services/ml_blind_protocol.py",
    "backend/app/services/ml_blind_guard.py",
    "backend/app/services/ml_blind_incumbent.py",
    "backend/app/services/ml_predictions_service.py",
    "backend/scripts/eval_nfl_ml_blind.py",
    "backend/tests/test_ml_blind_features.py",
    "backend/tests/test_ml_blind_protocol.py",
    "backend/tests/test_ml_blind_guard.py",
)


class LockRefusal(RuntimeError):
    """A fit or score was refused. Nothing was overwritten."""


@dataclass(frozen=True)
class GitState:
    head: str
    dirty: bool


def read_git_state(repo: Path = REPO_ROOT) -> GitState:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()
    porcelain = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo, text=True,
    )
    return GitState(head=head, dirty=bool(porcelain.strip()))


def assert_fit_allowed(
    locked_sha: str | None,
    *,
    repo: Path = REPO_ROOT,
    results_path: Path = RESULTS_PATH,
    spec_path: Path = SPEC_PATH,
    git_state: GitState | None = None,
    games_path: Path | None = None,
    pbp_paths: list[Path] | None = None,
    code_paths: tuple[str, ...] = CODE_PATHS,
) -> dict:
    """Require an external SHA, a clean tracked tree, and spec equality.

    Refuses when ``RESULTS.json`` already exists. Does not delete or rewrite it.
    """
    if results_path.exists():
        raise LockRefusal("RESULTS.json exists; refusing to fit or overwrite")
    if locked_sha is None or len(locked_sha) != 40 or any(c not in "0123456789abcdefABCDEF" for c in locked_sha):
        raise LockRefusal("external QA-locked commit SHA required")
    state = git_state if git_state is not None else read_git_state(repo)
    if state.head.lower() != locked_sha.lower():
        raise LockRefusal("HEAD does not match the QA-locked SHA")
    if state.dirty:
        raise LockRefusal("tracked tree is not clean")
    on_disk = json.loads(spec_path.read_text())
    if on_disk != spec_document():
        raise LockRefusal("TRAIN_SPEC does not match runtime constants")
    code_hash = _hash_paths(repo, code_paths)
    return {
        "locked_sha": locked_sha.lower(),
        "head": state.head.lower(),
        "spec_sha256": _sha256_file(spec_path),
        "code_sha256": code_hash,
        "games_sha256": None if games_path is None else _sha256_file(games_path),
        "pbp_sha256": {path.name: _sha256_file(path) for path in (pbp_paths or [])},
    }


def write_results_once(path: Path, payload: str) -> None:
    """Write a results file only when it does not already exist."""
    if path.exists():
        raise LockRefusal("RESULTS.json exists; refusing to overwrite")
    path.write_text(payload)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _hash_paths(repo: Path, relative: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in sorted(relative):
        file_path = repo / name
        if not file_path.is_file():
            raise LockRefusal(f"lock snapshot is missing {name}")
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
