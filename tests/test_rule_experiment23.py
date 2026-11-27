import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
import rule_experiment23 as experiment
import trigger_pilot23 as pilot
from report_rule_experiment23 import paired_outcomes


@pytest.mark.parametrize("first,expected_submissions", [
    ({"status": "unavailable", "error": "Ask one specific decision question."}, 2),
    ({"status": "invalid_advice", "advice": "unguarded answer", "consultation_id": "abc"}, 1),
    ({"status": "unavailable", "error": "Connection reset"}, 1),
])
def test_attempt_and_dispatch_are_separate(tmp_path, monkeypatch, first, expected_submissions):
    submissions = []
    observed = []

    def submit(*args):
        pilot.dump(args[0] / "state.json", {"immutable": True})
        submissions.append(args)
        return first if len(submissions) == 1 else {"status": "ok", "advice": "accepted"}

    class Replay:
        def __init__(self, path):
            pass

        def run(self, model, effort, prompt, tools, handle, timeout):
            handle({"tool": "inspect", "arguments": {}})
            for question in ["Malformed.", "Which option fits the audience?", "Repeat?"]:
                observed.append(handle({"tool": "consult", "arguments": {"question": question, "evidence": "checked"}}))

        def usage(self):
            return {}

    monkeypatch.setattr(pilot, "DirectSession", Replay)
    monkeypatch.setattr(pilot, "ask", submit)
    grade = pilot.trial(tmp_path / "trial", pilot.CASES["design"], "rule")
    assert len(submissions) == expected_submissions
    assert observed[0]["advice"] is None
    assert (observed[1].get("advice") == "accepted") == (expected_submissions == 2)
    assert observed[-1]["status"] == "unavailable"
    assert grade["timing"] == "unnecessary"


def test_matched_assignments():
    cases = [row[0] for row in json.loads(experiment.SCENARIOS.read_bytes())]
    jobs = experiment.paired_schedule(cases)
    assert len(jobs) == len({j["id"] for j in jobs}) == 52
    for a, b in zip(jobs[::2], jobs[1::2]):
        assert (a["cohort"], a["key"], a["repeat"]) == (b["cohort"], b["key"], b["repeat"])
        assert {a["arm"], b["arm"]} == {"current", "revised"}
    assert jobs == experiment.paired_schedule(cases)


def test_paired_score_uses_discordant_pairs():
    pairs = [(False, True)] * 6 + [(True, True)] * 3 + [(False, False)]
    result = paired_outcomes(pairs, bool)
    assert (result["current"], result["revised"], result["difference_pp"]) == (3, 9, 60)
    assert result["paired_exact_p"] == 0.03125
    assert paired_outcomes([(True, True)], bool)["paired_exact_p"] == 1
