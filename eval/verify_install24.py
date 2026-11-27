"""Exercise the public installer and rollback with an actual historical 2.2 installation."""

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from gap_study24 import ROOT


def verify():
    command = [sys.executable, "-B", str(ROOT / "scripts/install_skill.py")]
    earlier = subprocess.check_output(
        ["git", "archive", "--format=zip", "01b2c87", "skills/advisor"], cwd=ROOT
    )
    statuses = {}
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        with zipfile.ZipFile(BytesIO(earlier)) as bundle:
            bundle.extractall(folder / "prior")
        old = folder / "prior/skills/advisor"
        for host in (".agents", ".claude"):
            target = folder / host / "skills/advisor"

            def install(*opts):
                return json.loads(
                    subprocess.check_output(command + [str(target), *map(str, opts)])
                )

            assert install("--restore-from", old)["previous"] is None
            legacy = (target / "scripts/ask_expert.py").read_bytes()
            current = install()
            doctor = json.loads(
                subprocess.check_output(
                    [
                        sys.executable,
                        "-B",
                        str(target / "scripts/ask_expert.py"),
                        "--doctor",
                    ]
                )
            )
            assert (
                doctor["version"] == "2.4.0"
                and Path(doctor["script"]) == target / "scripts/ask_expert.py"
            )
            assert (
                Path(current["previous"]) / "scripts/ask_expert.py"
            ).read_bytes() == legacy
            prior = install("--restore-from", current["previous"])
            assert (target / "scripts/ask_expert.py").read_bytes() == legacy
            assert (Path(prior["previous"]) / "SKILL.md").read_bytes() == (
                ROOT / "skills/advisor/SKILL.md"
            ).read_bytes()
            statuses[host] = {
                "clean_install": True,
                "upgraded": True,
                "rollback_exact": True,
                "reported_version": doctor["version"],
            }
    return statuses


if __name__ == "__main__":
    print(json.dumps(verify()))
