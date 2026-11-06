"""JSONL 数据集加载与校验。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ("id", "repo", "title", "diff", "human_comments")


def _error(path: Path, lineno: int, message: str) -> ValueError:
    return ValueError(f"{path}:{lineno}: {message}")


def _require_text(value: Any, field: str, path: Path, lineno: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise _error(path, lineno, f"字段 {field!r} 必须是非空字符串")


def load(path: str | Path) -> list[dict[str, Any]]:
    """读取 JSONL 数据集。

    - 空行和以 ``#`` 开头的注释行会被跳过。
    - 每个对象必须包含 id/repo/title/diff/human_comments。
    - 任何格式错误都会抛出带文件名和行号的 ``ValueError``。
    """

    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"数据集不存在: {dataset_path}")
    if not dataset_path.is_file():
        raise ValueError(f"数据集不是文件: {dataset_path}")

    items: list[dict[str, Any]] = []

    with dataset_path.open("r", encoding="utf-8-sig") as fh:
        for lineno, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _error(
                    dataset_path,
                    lineno,
                    f"JSON 解析失败: {exc.msg}（第 {exc.colno} 列）",
                ) from exc

            if not isinstance(item, dict):
                raise _error(dataset_path, lineno, "每一行必须是 JSON 对象")

            missing = [field for field in REQUIRED_FIELDS if field not in item]
            if missing:
                raise _error(
                    dataset_path,
                    lineno,
                    "缺少必填字段: " + ", ".join(missing),
                )

            for field in ("id", "repo", "title", "diff"):
                _require_text(item[field], field, dataset_path, lineno)

            comments = item["human_comments"]
            if not isinstance(comments, list):
                raise _error(dataset_path, lineno, "字段 'human_comments' 必须是数组")

            for index, comment in enumerate(comments, start=1):
                prefix = f"human_comments[{index}]"
                if not isinstance(comment, dict):
                    raise _error(dataset_path, lineno, f"{prefix} 必须是 JSON 对象")
                for field in ("file", "line", "body"):
                    if field not in comment:
                        raise _error(
                            dataset_path,
                            lineno,
                            f"{prefix} 缺少必填字段: {field}",
                        )
                _require_text(comment["file"], f"{prefix}.file", dataset_path, lineno)
                _require_text(comment["body"], f"{prefix}.body", dataset_path, lineno)
                line_no = comment["line"]
                if (
                    isinstance(line_no, bool)
                    or not isinstance(line_no, int)
                    or line_no < 1
                ):
                    raise _error(
                        dataset_path,
                        lineno,
                        f"{prefix}.line 必须是大于等于 1 的整数",
                    )

            items.append(item)

    return items
