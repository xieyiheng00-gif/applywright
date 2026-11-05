import json
import shutil
import tempfile
import time
from datetime import date
from pathlib import Path

from jobfill import db
from jobfill.store import Store

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
TODAY = date(2026, 10, 27)


def _after(owner, function, *args) -> None:
    """Call function after the test, or after the whole class when owner is the class."""
    (owner.addClassCleanup if isinstance(owner, type) else owner.addCleanup)(function, *args)


def fresh_store(with_profile: bool = False, owner=None) -> Store:
    """In-memory store; pass the TestCase (or class) as owner to close it afterwards."""
    store = Store(db.connect(":memory:"))
    if owner is not None:
        _after(owner, store.conn.close)
    if with_profile:
        patch = json.loads((EXAMPLES / "sample_profile.json").read_text(encoding="utf-8"))
        store.import_patch(patch, dry_run=False)
    return store


def remove_dir(path: Path, tries: int = 20, wait: float = 0.25) -> None:
    """Delete a test's folder. On Windows a browser that just closed holds its profile for a moment, so try
    again for a few seconds, then leave what is left: a folder that stays must not fail the test."""
    for _ in range(tries):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            return
        time.sleep(wait)


def temp_dir(owner) -> Path:
    """A new folder, deleted after the test (or after the class, when owner is the class).

    Cleanups run last-added first, so ask for the folder before adding the cleanup that closes whatever uses it."""
    path = Path(tempfile.mkdtemp())
    _after(owner, remove_dir, path)
    return path
