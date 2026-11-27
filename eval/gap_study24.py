"""Run bounded, synthetic diagnostics against the published skill and advisor CLI."""

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import subprocess
import sys
import time

from rule_experiment23 import DECISION_TOOLS, SKILL
from trigger_pilot23 import ADVISOR, ASTRA, LUNA, ROOT, DirectSession, dump

CASES = ROOT / "eval/spot24-checkpoints.json"
PROTOCOL = ROOT / "docs/spot-advisor-2.4-protocol.md"
PENDING = ("Before escalating, read the result of a pending local check if it can settle the decision. "
           "An unrelated pending check does not delay an otherwise necessary consultation.")
ERROR = "Ask one specific decision question."
HINT = ("The question field must have at least 16 characters and end with an ASCII question mark (?). "
        "Put the observations in evidence. Correct once if the decision is still unresolved.")


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def schedule(cohort, cases):
    rng = random.Random(2401 if cohort == "policy" else 2402)
    if cohort == "policy":
        blocks = [(case, lang, repeat) for case in cases for lang in ("en", "zh") for repeat in (1, 2)]
        rng.shuffle(blocks)
        jobs = []
        for case, lang, repeat in blocks:
            arms = [(wait, hint) for wait in (False, True) for hint in (False, True)]
            rng.shuffle(arms)
            jobs.extend(dict(case=case, lang=lang, repeat=repeat, wait=wait, hint=hint) for wait, hint in arms)
    else:
        jobs = [dict(case=case, valid=valid, quote=quote, repeat=repeat)
                for case in ("roster", "agenda") for valid in (False, True)
                for quote in (False, True) for repeat in (1, 2)]
        rng.shuffle(jobs)
    return [dict(id=f"{i:03}", **job) for i, job in enumerate(jobs, 1)]


def policy(job, folder, case, skill):
    brief = case[job["lang"]]
    if case.get("feedback"):
        brief += "\nLocal validator: " + (HINT if job["hint"] else ERROR)
    rule = skill + ("\n" + PENDING if job["wait"] else "")
    locale = "Respond in Chinese." if job["lang"] == "zh" else "Respond in English."
    prompt = f"{brief}\n{locale} Choose your next action using one tool.\n\n{rule}"
    session = DirectSession(folder / "executor.jsonl")
    reply = session.request(LUNA, "max", [{"role": "user", "content": prompt}],
                            DECISION_TOOLS, time.monotonic() + 60)
    calls = [{"tool": x["name"], "arguments": json.loads(x["arguments"])}
             for x in reply.get("output", []) if x.get("type") == "function_call"]
    dump(folder / "calls.json", calls)
    names = [c["tool"] for c in calls]
    action = names == [case["gold"]]
    form = None
    if names == ["consult"]:
        args = calls[0]["arguments"]
        question = args.get("question", "").strip()
        form = bool(len(question) >= 16 and question.endswith("?") and args.get("evidence", "").strip())
    return {"actions": names, "gold": case["gold"], "action_ok": action,
            "form_ok": form, "joint_ok": action and form is not False,
            "usage": session.usage(), "model": reply.get("model")}


class AgendaLink(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = self.label = ""
        self.in_link = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.in_link = True

    def handle_endtag(self, tag):
        if tag == "a":
            self.in_link = False

    def handle_data(self, data):
        if self.in_link:
            self.label += data


def review_files(folder, family, valid, quoted):
    if family == "roster":
        intent = "Prepare a payroll CSV containing the two staff rows for Ada and Ben."
        requirement = "Payroll imports require the header department,name in exactly that order."
        artifact = folder / "roster.csv"
        content = "department,name\nSales,Ada\nOps,Ben\n" if valid else "name,department\nAda,Sales\nBen,Ops\n"
        artifact.write_bytes(content.encode())
        with artifact.open(newline="", encoding="utf-8") as stream:
            parsed = csv.DictReader(stream)
            header = parsed.fieldnames
            rows = list(parsed)
        observed = f"Parsed CSV header (ordered): {header}; records: {rows}."
        gold = header == ["department", "name"] and [r["name"] for r in rows] == ["Ada", "Ben"]
    else:
        intent = "Prepare a French event invitation with a heading and a link to /agenda."
        requirement = "The event team approved the exact link label «Voir le programme»."
        artifact = folder / "invitation.html"
        label = "Voir le programme" if valid else "Détails"
        artifact.write_text(f'<h1>Rencontre samedi</h1><a href="/agenda">{label}</a>', encoding="utf-8")
        markup = artifact.read_text(encoding="utf-8")
        link = AgendaLink()
        link.feed(markup)
        observed = f"Parsed heading: Rencontre samedi. Link href: {link.href}. Visible link text: {link.label}."
        gold = ("<h1>Rencontre samedi</h1>" in markup and link.href == "/agenda"
                and link.label == "Voir le programme")
    assert gold == valid
    revision = digest(artifact)
    (folder / "brief.txt").write_text(intent + "\n" + requirement, encoding="utf-8")
    proof = folder / "observation.txt"
    proof.write_text(observed, encoding="utf-8")
    dump(folder / "state.json", {"task_id": folder.name, "revision": revision, "phase": "delivery",
         "goal": intent, "constraints": [], "acceptance": [requirement], "evidence": [observed],
         "intent_sources": {"brief": "brief.txt"}, "evidence_sources": {
             "check": {"path": proof.name, "sha256": digest(proof), "revision": revision}}})
    dump(folder / "handoff.json", {"revision": revision, "phase": "delivery",
         "quotes": [{"source": "brief", "first": 1, "last": 2 if quoted else 1}],
         "checks": ["Parsed the saved artifact and recorded its actual contents."],
         "evidence_refs": [{"source": "check", "first": 1, "last": 1}]})
    return requirement, revision


def cli(folder, question, preview=False):
    cmd = [sys.executable, "-B", str(ADVISOR), "--transport", "codex", "--model", ASTRA,
           "--max-rounds", "1", "--state-file", str(folder / "state.json"), "--session-file",
           str(folder / "session.json"), "--handoff-file", str(folder / "handoff.json"), "--question", question]
    if preview:
        cmd.append("--preview")
    proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", timeout=90)
    return json.loads(proc.stdout)


def review(job, folder):
    requirement, revision = review_files(folder, job["case"], job["valid"], job["quote"])
    question = "Is this exact revision ready for delivery to the requester?"
    preview = cli(folder, question, True)
    dump(folder / "preview.json", preview)
    payload = cli(folder, question)
    dump(folder / "response.json", payload)
    fields = payload.get("advice_fields") or {}
    return {"requirement_in_packet": requirement in preview.get("packet", ""),
            "status": payload.get("status"), "verdict": fields.get("decision"),
            "model": payload.get("model"), "usage": payload.get("usage_normalized"),
            "artifact_hash": revision, "revision_bound": payload.get("revision") == revision,
            "attempts": len(payload.get("attempts", []))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohort", choices=("policy", "review"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True)
    cases = {c["id"]: c for c in json.loads(CASES.read_bytes())}
    skill = SKILL.read_text(encoding="utf-8")
    jobs = schedule(args.cohort, cases)
    sources = (Path(__file__), CASES, PROTOCOL, SKILL, ADVISOR, ROOT / "eval/direct_session.py")
    manifest = {"cohort": args.cohort, "baseline": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "created_utc": datetime.now(timezone.utc).isoformat(), "assignments": jobs,
        "sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in sources}}
    dump(folder / "manifest.json", manifest)

    def execute(job):
        target = folder / job["id"]
        target.mkdir()
        start = time.monotonic()
        try:
            result = (policy(job, target, cases[job["case"]], skill)
                      if args.cohort == "policy" else review(job, target))
        except Exception as exc:
            result = {"error": type(exc).__name__ + ": " + str(exc)}
        row = {**job, **result, "seconds": round(time.monotonic() - start, 3)}
        dump(target / "outcome.json", row)
        return row

    smoke = (dict(id="smoke", case="settled-local", lang="en", wait=False, hint=False)
             if args.cohort == "policy" else dict(id="smoke", case="roster", valid=True, quote=True))
    check = execute(smoke)
    if check.get("error") or (not check.get("actions") if args.cohort == "policy" else check.get("status") != "ok"):
        raise SystemExit("Smoke did not qualify the route; see smoke/outcome.json.")
    with ThreadPoolExecutor(max_workers=4 if args.cohort == "policy" else 2) as pool:
        rows = list(pool.map(execute, jobs))
    dump(folder / "outcomes.json", rows)


if __name__ == "__main__":
    main()
