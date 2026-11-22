"""Run a disjoint four-arm screenshot-consultation pilot on the Codex subscription."""

import argparse
import base64
import hashlib
import json
import random
import re
import shutil
import time
from pathlib import Path

from direct_session import DirectSession, ExecutorError
from report import BILLING, estimate
from run import ASTRA, LUNA, ROOT, dump
from visual22 import PAGE, render

ARMS = ("luna_only", "luna_text_advice", "luna_image_advice", "astra_only")
SEED = 20261121
GOAL = ("On this mobile signup page, make the intended 'Start free trial' action the clearest next step. "
        "Retain the visible product tour and functional trial. Preserve colors that already work. "
        "An already clear page may be left unchanged.")
ADVISOR = (f"{GOAL}\nThe page contains a title, short description, product-tour button and trial button. "
           "Both buttons fit the viewport and the trial action works. "
           "Return one paragraph with Verdict, Do, Verify and Risk. Recommend at most one change. "
           "Identify the visible competitor if the evidence supports it. If the hierarchy is already clear, say so. "
           "If evidence is insufficient, identify what the executor should inspect.")
CLEAR = """
#tour { background: white; color: #123456; font-weight: normal; }
#try { background: #123456; color: white; font-weight: bold; }
"""


def page_with_css(html: str, css: str) -> str:
    if len(css) > 2400 or re.search(r"[<>]|@|\\|\burl\s*\(", css, re.IGNORECASE):
        raise ValueError("Expected bounded local CSS.")
    return html.replace("</style>", f"\n{css}\n</style>", 1) if css else html


def ask(session: DirectSession, prompt: str, model: str, effort: str, deadline: float,
        screenshot: Path | None = None) -> str:
    content = [{"type": "input_text", "text": prompt}]
    if screenshot:
        encoded = base64.b64encode(screenshot.read_bytes()).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"})
    result = session.request(model, effort, [{"role": "user", "content": content}], [], deadline)
    return "".join(part["text"] for item in result.get("output", []) if item.get("type") == "message"
                   for part in item.get("content", []) if part.get("type") == "output_text").strip()


def run_arm(folder: Path, arm: str, html: str, screenshot: Path) -> dict:
    folder.mkdir()
    start = time.monotonic()
    deadline = start + 180
    session = DirectSession(folder / "attempts.jsonl")
    model, effort = (ASTRA, "medium") if arm == "astra_only" else (LUNA, "max")
    advice, patch, error = None, "", None
    if arm in {"luna_image_advice", "luna_text_advice"}:
        try:
            advice = ask(session, ADVISOR, ASTRA, "medium", deadline,
                         screenshot if arm == "luna_image_advice" else None)
            (folder / "advice.txt").write_text(advice, encoding="utf-8")
        except (ExecutorError, TimeoutError) as exc:
            error = str(exc)
    prompt = f"{GOAL}\nReturn only CSS rules to append, or exactly NO_CHANGE. Preserve the existing DOM.\n{html}"
    if advice:
        prompt += f"\nOne advisory reply to evaluate against the page.\n{advice}"
    try:
        response = ask(session, prompt, model, effort, deadline,
                       screenshot if arm in {"astra_only", "luna_with_image"} else None)
        (folder / "executor-response.txt").write_text(response, encoding="utf-8")
        if not response:
            raise ValueError("Executor returned no edit decision.")
        if response != "NO_CHANGE":
            patch = re.sub(r"\A```(?:css)?\s*|\s*```\Z", "", response, flags=re.IGNORECASE)
        final = page_with_css(html, patch)
    except (ExecutorError, TimeoutError, ValueError) as exc:
        error = "; ".join(filter(None, (error, str(exc))))
        final = html
    _, grade = render(folder, final)
    attempts = session.attempts
    prices = [estimate(row) for row in attempts]
    usage = [row["usage"] for row in attempts]
    complete = bool(usage) and all(row and row.get("input_tokens") is not None
                                   and row.get("output_tokens") is not None for row in usage)
    totals = {}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"):
        totals[key] = sum(row[key] for row in usage) if complete and all(row.get(key) is not None for row in usage) else None
    astra = [a["usage"] for a in attempts if a["requested_model"] == ASTRA]
    counts = {
        "total_tokens": totals["input_tokens"] + totals["output_tokens"] if complete else None,
        "frontier_tokens": sum(a["input_tokens"] + a["output_tokens"] for a in astra) if complete else None,
        "estimated_api_usd": sum(prices) if prices and all(p is not None for p in prices) else None,
    }
    result = {"arm": arm, "error": error, "advice_received": bool(advice), "changed": html != final,
              "wall_seconds": round(time.monotonic() - start, 3), "functional_pass": all(grade["checks"].values()),
              "requests_observed": len(attempts), "usage": {**totals, **counts}, **grade}
    dump(folder / "result.json", result)
    return result


def pilot(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    blinded = output / "blinded"
    blinded.mkdir()
    rng = random.Random(SEED)
    cases = {"competing-actions": PAGE, "already-clear": page_with_css(PAGE, CLEAR)}
    orders = {case: rng.sample(ARMS, len(ARMS)) for case in cases}
    dump(output / "manifest.json", {"seed": SEED, "stage": "disjoint_visual_pilot", "arms_by_case": orders,
         "initial_sha256": {case: hashlib.sha256(html.encode()).hexdigest() for case, html in cases.items()},
         "tool_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                         for name in ("visual_pilot22.py", "visual22.py", "browser_driver.py")},
         "pricing": BILLING, "execution_budget_seconds": 180, "viewport": [390, 844]})
    ratings_key = {}
    for case, html in cases.items():
        target = output / case
        starting = target / "starting"
        starting.mkdir(parents=True)
        picture, baseline = render(starting, html)
        dump(starting / "checks.json", baseline)
        if not all(baseline["checks"].values()):
            raise ValueError(f"Baseline failed checks for {case}.")
        for panel, arm in enumerate(orders[case], start=1):
            result = run_arm(target / arm, arm, html, picture)
            name = f"view-{rng.getrandbits(48):012x}.png"
            shutil.copyfile(target / arm / "mobile.png", blinded / name)
            ratings_key[name] = {"case": case, "arm": arm, "panel": panel}
            print(f"{case} / {arm}: functional_pass={result['functional_pass']}, error={result['error']}", flush=True)
    dump(output / "review-key.json", ratings_key)
    for panel in range(1, 5):
        names = sorted(name for name, record in ratings_key.items() if record["panel"] == panel)
        rng.shuffle(names)
        lines = ["# First-click review", "",
                 "Open one screenshot at a time. You are visiting the site to start a free trial. "
                 "Record the first element you would tap, or 'unclear'. Record the filename with your response.", ""]
        lines.extend(f"{i}. [Open screenshot]({name})" for i, name in enumerate(names, 1))
        (blinded / f"panel-{panel}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def image_control(output: Path, pilot_run: Path) -> None:
    """Try Luna with the same screenshot in an exploratory follow-up."""
    original = (pilot_run / "manifest.json").read_bytes()
    manifest = json.loads(original)
    if manifest["stage"] != "disjoint_visual_pilot":
        raise ValueError("Use a completed visual pilot.")
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "manifest.json", {"stage": "exploratory_visual_control", "arm": "luna_with_image",
         "original_manifest_sha256": hashlib.sha256(original).hexdigest(), "model": LUNA, "effort": "max"})
    for case in manifest["arms_by_case"]:
        starting = pilot_run / case / "starting"
        html = (starting / "index.html").read_text(encoding="utf-8")
        picture = starting / "mobile.png"
        previous = json.loads((starting / "checks.json").read_bytes())
        if (hashlib.sha256(html.encode()).hexdigest() != manifest["initial_sha256"][case]
                or hashlib.sha256(picture.read_bytes()).hexdigest() != previous["snapshot_sha256"]):
            raise ValueError(f"The starting screenshot or page changed: {case}")
        target = output / case
        target.mkdir()
        result = run_arm(target / "luna_with_image", "luna_with_image", html, picture)
        print(f"{case}: functional_pass={result['functional_pass']}, error={result['error']}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-control-for", type=Path, help="Run a supplementary Luna-with-image arm.")
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT / "eval/runs" not in output.parents:
        parser.error("Use a new directory under eval/runs.")
    if args.image_control_for:
        image_control(output, args.image_control_for)
    else:
        pilot(output)
