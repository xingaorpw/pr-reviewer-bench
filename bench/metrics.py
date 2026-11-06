"""可解释的评审指标。

不做"像不像"这种模糊判断，只算三个能说清楚的东西：

1. ``file_recall``    人工意见提到的文件，reviewer 有没有覆盖到
2. ``keyword_recall`` 人工意见里的关键问题词，reviewer 有没有说中
3. ``precision_proxy`` reviewer 提的意见，有多少落在 diff 新增行范围内

``score = 0.4 * file_recall + 0.4 * keyword_recall + 0.2 * precision_proxy``
"""

from __future__ import annotations

import re
from typing import Any

WEIGHTS = {"file_recall": 0.4, "keyword_recall": 0.4, "precision_proxy": 0.2}

STOPWORDS = {
    "the", "a", "an", "to", "and", "or", "of", "in", "on", "for", "is", "are",
    "be", "this", "that", "it", "as", "at", "by", "with", "from", "if", "not",
    "这里", "这个", "那个", "可以", "需要", "应该", "一个", "没有", "会", "要",
    "把", "被", "在", "是", "的", "了", "和", "与", "或", "就", "都", "也",
}

_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def parse_added(diff: str) -> list[dict[str, Any]]:
    """解析 unified diff，返回所有新增行：``[{file, line, text}]``。"""
    rows: list[dict[str, Any]] = []
    current: str | None = None
    new_line = 0

    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current = path
            continue

        if raw.startswith("--- ") or raw.startswith("diff --git"):
            continue

        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            if match:
                new_line = int(match.group(1))
            continue

        if current is None:
            continue

        if raw.startswith("+"):
            rows.append({"file": current, "line": new_line, "text": raw[1:]})
            new_line += 1
        elif raw.startswith("-"):
            continue
        else:
            new_line += 1

    return rows


def parse_added_lines(diff: str) -> dict[str, list[int]]:
    """从 unified diff 中解析出「每个文件新增了哪些行号」。"""
    added: dict[str, list[int]] = {}
    for row in parse_added(diff):
        added.setdefault(row["file"], []).append(row["line"])
    return added


def keywords(text: str) -> set[str]:
    """抽取关键问题词。

    英文取 3 字符以上的词（去停用词）；中文切成 bigram——
    否则「比较」和「比较请用」会被当成两个不同的词，永远匹配不上。
    """
    found: set[str] = set()
    raw = str(text or "")

    for match in _EN_RE.finditer(raw):
        word = match.group(0).lower()
        if word in STOPWORDS:
            continue
        found.add(word)

    for match in _CJK_RE.finditer(raw):
        run = match.group(0)
        if len(run) < 2:
            continue
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram in STOPWORDS:
                continue
            found.add(bigram)

    return found


def _file_recall(human: list[dict], review: list[dict]) -> float:
    if not human:
        return 1.0
    wanted = {c["file"] for c in human}
    got = {c["file"] for c in review}
    return len(wanted & got) / len(wanted)


def _keyword_recall(human: list[dict], review: list[dict]) -> float:
    if not human:
        return 1.0
    review_words: set[str] = set()
    for comment in review:
        review_words |= keywords(comment.get("body", ""))

    if not review_words:
        return 0.0

    per_comment = []
    for comment in human:
        wanted = keywords(comment.get("body", ""))
        if not wanted:
            per_comment.append(0.0)
            continue
        per_comment.append(len(wanted & review_words) / len(wanted))

    return sum(per_comment) / len(per_comment)


def _precision_proxy(diff: str, review: list[dict]) -> float:
    if not review:
        return 0.0

    added = parse_added_lines(diff)
    hits = 0
    for comment in review:
        lines = added.get(comment.get("file", ""))
        if not lines:
            continue
        if comment.get("line") in lines:
            hits += 1

    return hits / len(review)


def evaluate(pr: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    """给一次 review 打分，返回三个指标、总分和明细。"""
    human = pr.get("human_comments") or []
    review = list(comments or [])

    file_recall = _file_recall(human, review)
    keyword_recall = _keyword_recall(human, review)
    precision_proxy = _precision_proxy(pr.get("diff", ""), review)

    score = (
        WEIGHTS["file_recall"] * file_recall
        + WEIGHTS["keyword_recall"] * keyword_recall
        + WEIGHTS["precision_proxy"] * precision_proxy
    )

    added = parse_added_lines(pr.get("diff", ""))
    in_scope = sum(
        1 for c in review if c.get("line") in added.get(c.get("file", ""), [])
    )

    return {
        "id": pr.get("id"),
        "file_recall": round(file_recall, 4),
        "keyword_recall": round(keyword_recall, 4),
        "precision_proxy": round(precision_proxy, 4),
        "score": round(max(0.0, min(1.0, score)), 4),
        "comments": len(review),
        "in_scope_comments": in_scope,
        "human_comments": len(human),
        "matched_files": sorted({c["file"] for c in review} & {c["file"] for c in human}),
    }


__all__ = ["evaluate", "parse_added", "parse_added_lines", "keywords", "WEIGHTS", "STOPWORDS"]
