"""命令行：run（跑评测）和 report（看排行榜）。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import dataset, metrics
from .reviewers import ReviewerError, build_reviewer

METRIC_KEYS = ("file_recall", "keyword_recall", "precision_proxy")


def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _avg(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(float(r.get(key, 0.0)) for r in rows) / len(rows)


def run_bench(dataset_path: str, reviewer_name: str, out_path: str | None) -> dict[str, Any]:
    items = dataset.load(dataset_path)
    if not items:
        raise ValueError(f"数据集为空：{dataset_path}")

    reviewer = build_reviewer(reviewer_name)
    rows: list[dict[str, Any]] = []

    for pr in items:
        comments = reviewer.review(pr)
        result = metrics.evaluate(pr, comments)
        rows.append(result)
        print(
            f"  {result['id']:<10} 分数 {result['score']:.2f}   "
            f"文件 {result['file_recall']:.2f}   "
            f"关键词 {result['keyword_recall']:.2f}   "
            f"精准 {result['precision_proxy']:.2f}   "
            f"{result['comments']} 条意见"
        )

    summary = {
        "reviewer": reviewer.name,
        "dataset": str(dataset_path),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prs": len(rows),
        "average": round(_avg(rows, "score"), 4),
        "metrics": {key: round(_avg(rows, key), 4) for key in METRIC_KEYS},
        "results": rows,
    }

    if out_path:
        Path(out_path).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n  结果已写入 {out_path}")

    return summary


def print_report(path: str) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    reviewer = data.get("reviewer", "?")
    average = float(data.get("average", 0.0))
    per_metric = data.get("metrics") or {}

    print(f"  reviewer      {reviewer}")
    print(f"  PR 数         {data.get('prs', 0)}")
    print(f"  平均分        {average:.3f}")
    print()
    print("  分项指标")
    for key in METRIC_KEYS:
        value = float(per_metric.get(key, 0.0))
        bar = "█" * max(0, round(value * 20))
        print(f"    {key:<16} {value:.3f}  {bar}")

    if per_metric:
        weakest = min(per_metric, key=lambda k: per_metric[k])
        print()
        print(f"  最弱的一项：{weakest}（{float(per_metric[weakest]):.3f}）")

    rows = data.get("results") or []
    if rows:
        print()
        print("  逐条")
        for row in rows:
            print(
                f"    {row['id']:<10} {row['score']:.2f}   "
                f"人工意见 {row['human_comments']} 条 / 机器意见 {row['comments']} 条"
            )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench",
        description="给 AI code review 打分：用带人工评审意见的真实 PR 做基准",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑一遍评测")
    run.add_argument("--dataset", default="data/seed.jsonl")
    run.add_argument("--reviewer", default="mock", choices=["mock", "openai"])
    run.add_argument("--out", default=None, help="把结果写入 JSON")
    run.set_defaults(func=_cmd_run)

    report = sub.add_parser("report", help="读取结果文件并打印报告")
    report.add_argument("path")
    report.set_defaults(func=_cmd_report)

    return parser


def _cmd_run(args: argparse.Namespace) -> int:
    print(f"pr-reviewer-bench · reviewer={args.reviewer} · {args.dataset}")
    print()
    try:
        summary = run_bench(args.dataset, args.reviewer, args.out)
    except FileNotFoundError as exc:
        print(f"数据集不存在：{exc}", file=sys.stderr)
        return 1
    except ReviewerError as exc:
        print(f"Reviewer 错误：{exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"数据错误：{exc}", file=sys.stderr)
        return 1

    print()
    print(f"  平均分 {summary['average']:.3f}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    try:
        return print_report(args.path)
    except FileNotFoundError:
        print(f"结果文件不存在：{args.path}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    _utf8()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


__all__ = ["main", "build_parser", "run_bench", "print_report"]
