"""Run PAL MCP's one-turn chat workflow with the matched Astra subscription model."""

import argparse
import asyncio
import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from direct_session import DirectSession, ExecutorError
from run import ASTRA, LUNA, ROOT, TOOLS, TaskExecution, dump, ensure_image, grade
from sandbox import Sandbox
from suites import load_swe_instances

PAL_REVISION = "7afc7c1cc96e23992c8f105f960132c657883bb1"


@contextmanager
def one_turn_provider(session: DirectSession, deadline: float):
    """Translate PAL's single text generation through a private, short-lived local endpoint."""
    token = secrets.token_hex(24)
    used = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def send_json(self, status, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            nonlocal used
            if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != f"Bearer {token}":
                self.send_json(403, {"error": {"message": "Unrecognized PAL provider request."}})
                return
            if used:
                self.send_json(409, {"error": {"message": "The assigned advisor turn was already used."}})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000:
                    raise ValueError("Request too large.")
                self.connection.settimeout(10)
                raw = json.loads(self.rfile.read(size))
                messages = raw["messages"]
                if (raw["model"] != ASTRA or raw.get("stream") or raw.get("tools")
                        or not isinstance(messages, list) or not messages
                        or any(msg["role"] not in {"system", "user", "assistant"}
                               or not isinstance(msg["content"], str) for msg in messages)):
                    raise ValueError("Expected one non-streaming, tool-free text request to Astra.")
                used = True
                response = session.request(ASTRA, "medium", messages, [], deadline)
                text = "".join(part["text"] for item in response.get("output", [])
                               if item.get("type") == "message" for part in item.get("content", [])
                               if part.get("type") == "output_text")
                if not text.strip():
                    raise ExecutorError("Astra returned no PAL reply.")
            except (KeyError, TypeError, ValueError, ExecutorError, TimeoutError) as exc:
                self.send_json(400, {"error": {"message": str(exc)}})
                return
            usage = session.attempts[-1]["usage"]
            inputs, outputs = usage["input_tokens"], usage["output_tokens"]
            self.send_json(200, {"id": response["id"], "object": "chat.completion", "created": int(time.time()),
                                 "model": response["model"], "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": text}}],
                                 "usage": {"prompt_tokens": inputs, "completion_tokens": outputs,
                                           "total_tokens": inputs + outputs if None not in (inputs, outputs) else None}})

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1", token
        finally:
            server.shutdown()
            thread.join()


async def pal_chat(source: Path, folder: Path, workdir: Path, prompt: str,
                   url: str, token: str, config: Path) -> str:
    env = {"CUSTOM_API_URL": url, "CUSTOM_API_KEY": token, "CUSTOM_MODELS_CONFIG_PATH": str(config),
           "DEFAULT_MODEL": ASTRA, "PAL_MCP_FORCE_ENV_OVERRIDE": "false", "PYTHONUTF8": "1"}
    params = StdioServerParameters(command=sys.executable, args=[str(source / "server.py")],
                                  cwd=str(workdir), env=env)
    with (folder / "pal-server.log").open("w", encoding="utf-8") as logs:
        async with stdio_client(params, errlog=logs) as (incoming, outgoing):
            async with ClientSession(incoming, outgoing) as client:
                await client.initialize()
                reply = await client.call_tool("chat", {"model": ASTRA, "prompt": prompt,
                                                       "working_directory_absolute_path": str(workdir)})
    dump(folder / "pal-receipt.json", reply.model_dump(mode="json"))
    if reply.isError:
        raise ExecutorError("PAL chat reported an error. Inspect pal-receipt.json.")
    return "\n".join(item.text for item in reply.content if item.type == "text")


def ask_pal(source: Path, folder: Path, prompt: str, deadline: float) -> str:
    session = DirectSession(folder / "pal-advisor-attempts.jsonl")
    models = {"models": [{"model_name": ASTRA, "aliases": [], "context_window": 128000,
                          "max_output_tokens": 16000, "supports_temperature": False,
                          "description": "Astra, one matched evaluation turn"}]}
    with tempfile.TemporaryDirectory(prefix="sa22-pal-") as work:
        workdir = Path(work)
        config = workdir / "models.json"
        dump(config, models)
        with one_turn_provider(session, deadline) as (url, token):
            timeout = min(100., max(1., deadline - time.monotonic()))
            reply = asyncio.run(asyncio.wait_for(pal_chat(source, folder, workdir, prompt, url, token, config),
                                                 timeout=timeout))
    if (not reply.strip() or len(session.attempts) != 1
            or session.attempts[0].get("status") != "completed"
            or session.attempts[0].get("model") != ASTRA):
        raise ExecutorError("PAL did not complete a matched Astra reply.")
    return reply


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", help="Frozen peer issue ID. Omit to qualify the provider route only.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--peer-source", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
    out, source = args.output.resolve(), args.peer_source.resolve()
    if ROOT / "eval/runs" not in out.parents or args.case and args.case not in manifest["peer_tasks"]:
        parser.error("Use a frozen peer case and an output under eval/runs.")
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != PAL_REVISION or subprocess.check_output(["git", "-C", str(source), "status", "--porcelain",
                                                           "--untracked-files=no"], text=True).strip():
        parser.error("Use the unmodified pinned PAL checkout.")
    if not args.case:
        out.mkdir(parents=True, exist_ok=False)
        marker = secrets.token_hex(5)
        reply = ask_pal(source, out, f"Repeat this qualification marker in your response: {marker}",
                        time.monotonic() + 100)
        dump(out / "qualification.json", {"product_revision": revision, "model": ASTRA, "matched": marker in reply})
        if marker not in reply:
            raise ExecutorError("PAL reply did not contain the qualification marker.")
        print("PAL chat returned the expected marker from Astra.")
        return
    task, = load_swe_instances(ROOT / "eval/runs/swe-bench-pro-source", [args.case])
    ensure_image(task)
    folder = out / task.id / "pal-mcp"
    folder.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    deadline = start + task.timeout
    brief = f"Task: {task.goal}\nConstraints: {task.constraints}\nAcceptance: {task.acceptance}"
    prompt = ("Review this ordinary maintenance issue for a coding agent. Identify one high-value next step "
              "and a check it can run. Request specific evidence if useful.\n" + brief)
    error = None
    try:
        advice = ask_pal(source, folder, prompt, deadline)
    except (ExecutorError, TimeoutError) as exc:
        error, advice = str(exc), ""
    with Sandbox(task.image) as workspace:
        workspace.initial(task, folder)
        executor = DirectSession(folder / "executor-attempts.jsonl")
        execution = TaskExecution(task, "luna_only", workspace, folder, deadline)
        handoff = f"{brief}\nUse the workspace to submit a patch. Verify this peer reply if present:\n{advice}"
        try:
            turn = executor.run(LUNA, "max", handoff, TOOLS, execution.handle, execution.remaining(task.timeout))
        except (TimeoutError, ExecutorError, subprocess.TimeoutExpired):
            turn = {"status": "executor_interrupted"}
        patch = workspace.patch()
    (folder / "model.patch").write_bytes(patch)
    accepted, verdict = grade(task, folder, patch, folder)
    dump(folder / "result.json", {
        "task": task.id, "peer": "pal-mcp", "product_revision": revision,
        "workflow": "PAL MCP chat through a one-turn Codex subscription adapter, then Luna reconciliation",
        "advisor_error": error, "accepted": accepted, "verdict": verdict, "turn_status": turn["status"],
        "executor_usage": executor.usage(), "wall_seconds": round(time.monotonic() - start, 3),
        "artifact_sha256": hashlib.sha256(patch).hexdigest(),
    })
    print(f"pal-mcp / {task.id}: {verdict}, advisor delivered={error is None}")


if __name__ == "__main__":
    main()
