"""Assemble only the static files intended for the GitHub Pages artifact."""

from pathlib import Path
import shutil


SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
DIST = SITE / "dist"

SOURCES = {
    SITE / "index.html": DIST / "index.html",
    SITE / "zh-CN/index.html": DIST / "zh-CN/index.html",
    SITE / "assets/site.css": DIST / "assets/site.css",
    ROOT / "docs/assets/spot-advisor-hero.webp": DIST / "assets/spot-advisor-hero.webp",
    ROOT / "docs/assets/spot-advisor-hero.zh-CN.webp": DIST / "assets/spot-advisor-hero.zh-CN.webp",
    ROOT / "demo/spot-advisor-demo.gif": DIST / "assets/spot-advisor-demo.gif",
    ROOT / "demo/spot-advisor-demo.zh-CN.gif": DIST / "assets/spot-advisor-demo.zh-CN.gif",
}


def main() -> None:
    expected_parent = SITE.resolve()
    resolved_dist = DIST.resolve()
    if resolved_dist.parent != expected_parent:
        raise ValueError("Pages output must stay directly inside the site source directory.")
    is_junction = getattr(DIST, "is_junction", lambda: False)
    if DIST.is_symlink() or is_junction():
        raise ValueError("Refusing to build through a linked Pages output directory.")
    missing = [source for source in SOURCES if not source.is_file()]
    if missing:
        raise FileNotFoundError("Required Pages source assets are missing: " + ", ".join(str(path) for path in missing))
    if DIST.exists():
        if not DIST.is_dir():
            raise ValueError("Pages output exists and is not a directory.")
        shutil.rmtree(DIST)
    for source, destination in SOURCES.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        print(f"Copied {source.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
