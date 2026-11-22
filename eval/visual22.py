"""Qualify the original mobile signup fixture and an explicitly chosen image route."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from direct_session import DirectSession, ExecutorError
from run import ADVISOR, dump
from sandbox import Sandbox, checked, docker
from suites import BROWSER_IMAGE

PAGE = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Studio trial</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 28px; font: 16px sans-serif; color: #14293a; background: #f1f5f2; }
main { max-width: 390px; margin: 38px auto; }
p { line-height: 1.5; }
button { font: inherit; display: block; width: 100%; padding: 14px; margin: 12px 0; border: 1px solid #123456;
         border-radius: 5px; color: #123456; background: #fff; cursor: pointer; }
#tour { background: #123456; color: white; font-weight: bold; }
.proof { position: fixed; right: 12px; top: 12px; font: bold 20px monospace; }
</style>
<main><h1>Plan your next session</h1><p>Try Studio free for a week.</p>
<button id="tour">See the product tour</button>
<button id="try" onclick="document.getElementById('confirmation').textContent='Trial started'">Start free trial</button>
<p id="confirmation" aria-live="polite"></p></main>
</html>"""
CHECK = """
from browser_driver import open_fixture, command, script, act
import json
open_fixture()
command("/goog/cdp/execute", {"cmd":"Emulation.setDeviceMetricsOverride",
    "params":{"width":390, "height":844, "deviceScaleFactor":1, "mobile":True}})
command("/refresh", {})
checks = script('''const b = document.querySelector('#try');
const r = b.getBoundingClientRect();
const style = getComputedStyle(b);
const tour = document.querySelector('#tour');
const t = tour.getBoundingClientRect();
return {no_horizontal_overflow: document.documentElement.scrollWidth === innerWidth,
        target_visible: r.width > 0 && r.height > 0 && r.bottom <= innerHeight,
        hit_target: document.elementFromPoint(r.x+r.width/2, r.y+r.height/2) === b,
        trial_label_readable: style.visibility === 'visible' && Number(style.opacity) >= .9
            && parseFloat(style.fontSize) >= 14 && r.height >= 44
            && b.scrollWidth <= b.clientWidth && b.scrollHeight <= b.clientHeight,
        tour_remains_visible: t.width > 0 && t.height > 0 && t.bottom <= innerHeight
            && getComputedStyle(tour).visibility === 'visible'
            && document.elementFromPoint(t.x+t.width/2, t.y+t.height/2) === tour};''')
image = command("/screenshot")
act("click", "Start free trial", None)
checks["click_confirms"] = script("return document.getElementById('confirmation').textContent") == "Trial started"
print(json.dumps({"checks":checks,"image":image}))
"""


def probe_codex(output: Path, model: str, image: Path, expected: str, effort: str = "medium") -> dict:
    """Check model image ingestion independently of the shipped advisor routing."""
    started = time.monotonic()
    session = DirectSession(output / "codex-image-attempts.jsonl")
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    messages = [{"role": "user", "content": [
        {"type": "input_text", "text": "Read the six-character code at the top right. Reply with only that code."},
        {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}", "detail": "high"},
    ]}]
    try:
        reply = session.request(model, effort, messages, [], started + 90)
        answer = "".join(part["text"] for item in reply.get("output", [])
                         if item.get("type") == "message" for part in item.get("content", [])
                         if part.get("type") == "output_text").strip()
        result = {"route": "qualified" if answer.upper() == expected else "unqualified",
                  "code_match": answer.upper() == expected}
    except (ExecutorError, TimeoutError) as exc:
        result = {"route": "unqualified", "request_error": str(exc)}
    attempt = session.attempts[-1] if session.attempts else {}
    return {**result, "probe": "direct_codex_image_ingestion", "requested_model": model, "reasoning_effort": effort,
            "reported_model": attempt.get("model"), "request_status": attempt.get("status"),
            "elapsed_seconds": round(time.monotonic() - started, 3), "usage": attempt.get("usage")}


def render(output: Path, html: str) -> tuple[Path, dict]:
    fixture = output / "index.html"
    fixture.write_text(html, encoding="utf-8")
    with Sandbox(BROWSER_IMAGE) as browser:
        checked(docker("exec", browser.container, "mkdir", "-p", "/work"))
        browser.copy(fixture, "/work/index.html")
        browser.copy(Path(__file__).with_name("browser_driver.py"), "/work/browser_driver.py")
        checked(browser.exec("chromedriver --port=9515 >/tmp/chromedriver.log 2>&1 &"))
        data = json.loads(checked(browser.exec("python -B -", data=CHECK.encode(), timeout=45)))
    image = output / "mobile.png"
    image.write_bytes(base64.b64decode(data.pop("image"), validate=True))
    data.update({"viewport": [390, 844], "snapshot_sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
    return image, data


def qualify(output: Path, model: str | None = None, codex: bool = False, effort: str = "medium") -> dict:
    output.mkdir(parents=True, exist_ok=False)
    expected = secrets.token_hex(3).upper()
    html = PAGE.replace("</html>", f'<span class="proof">{expected}</span></html>') if model else PAGE
    image, data = render(output, html)
    data["route"] = "unqualified"
    dump(output / ("fixture-checks.json" if model else "qualification.json"), data)
    if model and codex:
        data.update(probe_codex(output, model, image, expected, effort))
        dump(output / "qualification.json", data)
    elif model:
        host = urlsplit(os.environ.get("SPOT_ADVISOR_VISION_BASE_URL", ""))
        if (host.scheme != "https" or not host.hostname or host.username or host.password
                or not os.environ.get("SPOT_ADVISOR_VISION_API_KEY")):
            raise ValueError("Set the HTTPS visual study route and its credential explicitly.")
        env = {**os.environ, "EXPERTS_API_KEY": os.environ["SPOT_ADVISOR_VISION_API_KEY"]}
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-B", str(ADVISOR), "--transport", "gateway",
                                 "--base-url", host.geturl(), "--model", model, "--image", str(image),
                                 "--session-file", str(output / "session.json"),
                                 "--question", "Read the six-character code at the top right. Include it in Verdict."],
                                env=env, capture_output=True, encoding="utf-8", timeout=240, check=False)
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError:
            receipt = {}
        data.update({"route": "qualified" if result.returncode == 0 and receipt.get("model") == model
                     and expected in receipt.get("advice", "") else "unqualified",
                     "requested_model": model, "reported_model": receipt.get("model"),
                     "request_status": receipt.get("status", "no_receipt"),
                     "request_error": receipt.get("error"),
                     "elapsed_seconds": round(time.monotonic() - started, 3),
                     "usage": receipt.get("usage_normalized")})
        dump(output / "qualification.json", data)
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="Qualify this vision model.")
    parser.add_argument("--codex", action="store_true", help="Probe image ingestion on the existing Codex subscription.")
    parser.add_argument("--effort", choices=("medium", "max"), default="medium",
                        help="Reasoning effort for the direct Codex probe.")
    args = parser.parse_args()
    if args.codex and not args.model:
        parser.error("--codex requires --model.")
    if args.effort != "medium" and not args.codex:
        parser.error("--effort applies to the direct Codex probe.")
    result = qualify(args.output.resolve(), args.model, args.codex, args.effort)
    print(json.dumps(result, indent=2))
    raise SystemExit(int(not all(result["checks"].values()) or bool(args.model and result["route"] != "qualified")))
