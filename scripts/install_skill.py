"""Install the bundled skill into a specific host location, retaining the previous copy."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile


def install(destination, source):
    if destination.name != "advisor" or destination.is_symlink():
        raise ValueError(
            "Choose an advisor skill directory, for example .agents/skills/advisor."
        )
    source = source.resolve(strict=True)
    if not all(
        (source / name).is_file() for name in ("SKILL.md", "scripts/ask_expert.py")
    ):
        raise ValueError("The selected source is missing the advisor skill or caller.")
    destination = destination.resolve()
    if (
        source == destination
        or source.is_relative_to(destination)
        or destination.is_relative_to(source)
    ):
        raise ValueError("Source and installation must be separate directories.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".install-advisor-"
    ) as staging:
        staged = Path(staging) / "advisor"
        shutil.copytree(
            source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            # Backups live outside the host's skill discovery directory.
            backups = destination.parent.parent / "advisor-backups"
            backups.mkdir(exist_ok=True)
            backup = backups / stamp
            destination.rename(backup)
        try:
            staged.rename(destination)
        except OSError:
            if backup and not destination.exists():
                backup.rename(destination)
            raise
    return {"installed": str(destination), "previous": str(backup) if backup else None}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--restore-from", type=Path, help="Reinstall a previously saved copy."
    )
    args = parser.parse_args()
    source = args.restore_from or Path(__file__).resolve().parents[1] / "skills/advisor"
    print(json.dumps(install(args.destination, source)))
