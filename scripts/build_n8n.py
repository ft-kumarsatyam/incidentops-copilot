"""Keep the JavaScript inside n8n/incident-ops.json equal to the files in n8n/src/.

The Code nodes' logic lives in real .js files so it can be read, linted and tested. n8n needs it
inlined in the workflow JSON, so this script copies it in.

    python scripts/build_n8n.py          # rewrite the workflow's jsCode fields
    python scripts/build_n8n.py --check  # exit 1 if the workflow is out of date (used in CI)
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "n8n" / "incident-ops.json"
SRC = ROOT / "n8n" / "src"

# node name -> source files, concatenated in order (the Redis helper is shared, so each node that
# talks to Redis embeds the same copy; a test pins that they stay identical).
NODES: dict[str, list[str]] = {
    "Verify Slack Signature": ["slack-verify.js"],
    "Action Idempotency Gate": ["redis-helper.js", "action-gate.js"],
    "Mark Action Done": ["redis-helper.js", "action-done.js"],
    "Release Action Claim": ["redis-helper.js", "action-release.js"],
}


def render(files: list[str]) -> str:
    return "\n".join((SRC / name).read_text(encoding="utf-8").rstrip("\n") for name in files) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the workflow is stale")
    args = parser.parse_args()
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    stale: list[str] = []
    for name, files in NODES.items():
        node = by_name.get(name)
        if node is None:
            print(f"workflow has no node named {name!r}", file=sys.stderr)
            return 2
        wanted = render(files)
        if node["parameters"].get("jsCode") != wanted:
            stale.append(name)
            node["parameters"]["jsCode"] = wanted
    if args.check:
        if stale:
            print(f"stale n8n nodes: {', '.join(stale)}. Run `make n8n-build` and commit.")
            return 1
        return 0
    WORKFLOW.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"updated: {', '.join(stale) or 'nothing (already current)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
