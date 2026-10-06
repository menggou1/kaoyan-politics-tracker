"""Consistent SQLite backups through SQLite's backup API."""

from datetime import date, datetime
from pathlib import Path

from .database import DEFAULT_DB, ROOT, connect, initialize


def backup(path=DEFAULT_DB, directory=None, manual=False):
    initialize(path)
    directory = Path(directory or ROOT / "backups")
    directory.mkdir(parents=True, exist_ok=True)
    prefix = "manual" if manual else "auto"
    day = date.today().isoformat()
    if not manual:
        existing = sorted(directory.glob(f"politics_auto_{day}_*.db"))
        if existing:
            return existing[-1]
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    target = directory / f"politics_{prefix}_{stamp}.db"
    with connect(path) as source, connect(target) as destination:
        source.backup(destination)
    if not manual:
        auto = sorted(directory.glob("politics_auto_*.db"))
        for old in auto[:-30]:
            old.unlink()
    return target
