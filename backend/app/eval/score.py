"""Score the close against the answer key.

    python -m app.eval.score [--world company|cast] [--period 2026-10] --runs 3 [--json report.json]

Every run starts from a fresh copy of the company. A case counts as caught only if all
runs handle it as the key expects, the same way each time. Exits 1 if any target is missed.
"""

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("VESTLY_SKIP_WORLD", "1")

from app.eval.runner import run_close  # noqa: E402
from app.eval.scorer import TARGETS, load_key, run_as_dict, score  # noqa: E402


def score_period(period: str, runs: int = 3, key: dict | None = None, model=None, world: str | None = None) -> tuple[dict, list]:
    from app.errors import doing

    results = []
    for number in range(1, runs + 1):
        with doing("running a scored close", period=period, run=number):
            results.append(run_close(period, model, world))
    return score(key or load_key(world=world or os.environ.get("VESTLY_WORLD", "company")), results), results


def render(report: dict) -> str:
    metrics = report["metrics"]
    mark = {True: "met", False: "MISSED"}
    lines = [
        f"Vestly close {report['period']}, {report['runs']} run(s) on a fresh copy each",
        "",
        f"  Caught             {metrics['caught']['value']}/{metrics['caught']['of']}"
        f"            target {metrics['caught']['of']}/{metrics['caught']['of']}   {mark[metrics['caught']['met']]}",
        f"  False holds        {metrics['false_holds']['value']}/{metrics['false_holds']['of']} clean items"
        f"   target < {TARGETS['false_holds']:.0%}      {mark[metrics['false_holds']['met']]}",
        f"  Evidence complete  {metrics['evidence_complete']['rate']:.0%}"
        f"             target 100%      {mark[metrics['evidence_complete']['met']]}",
        f"  Money correct      {metrics['money_correct']['rate']:.0%}"
        f"             target 100%      {mark[metrics['money_correct']['met']]}",
        f"  Never              {metrics['never']['value']}"
        f"                target 0         {mark[metrics['never']['met']]}",
        "",
    ]
    for case in report["cases"]:
        lines.append(f"  {case['id']} {'caught' if case['caught'] else 'MISSED'}  {case['name']}")
        lines.extend(f"      {problem}" for problem in case["problems"])
    if metrics["false_holds"]["subjects"]:
        lines.append(f"  False holds on: {', '.join(metrics['false_holds']['subjects'])}")
    lines.extend(f"  Money: {problem}" for problem in metrics["money_correct"]["problems"])
    lines.extend(f"  Forbidden: {action}" for action in metrics["never"]["actions"])
    lines.append("")
    lines.append("PASSED" if report["passed"] else "FAILED")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score Vestly's close against the answer key.")
    parser.add_argument("--world", choices=("company", "cast"), default=os.environ.get("VESTLY_WORLD", "company"))
    parser.add_argument("--period", help="the close to score (default: 2026-10 for the company world, 2026-06 for the cast)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", type=Path, help="Also write the full report and run results here.")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    period = args.period or ("2026-10" if args.world == "company" else "2026-06")
    report, results = score_period(period, args.runs, world=args.world)
    print(render(report))
    if args.json:
        from datetime import datetime, timezone

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "command": f"python -m app.eval.score --world {args.world} --period {period} --runs {args.runs}",
            "world": args.world,
            "exit_code": 0 if report["passed"] else 1,
            "report": report,
            "runs": [run_as_dict(run) for run in results],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
