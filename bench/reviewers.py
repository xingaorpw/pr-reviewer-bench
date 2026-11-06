"""Reviewer 抽象与两个实现。

``MockReviewer`` 是确定性的规则引擎，用来验证评测流程本身是否正常；
``OpenAICompatReviewer`` 接真实模型，没配环境变量时构造就会失败。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .metrics import parse_added


class ReviewerError(RuntimeError):
    """Reviewer 配置或调用失败。"""


class Reviewer:
    """Reviewer 抽象基类。"""

    name = "base"

    def review(self, pr: dict[str, Any]) -> list[dict[str, Any]]:
        """返回 ``[{file, line, body}]``。"""
        raise NotImplementedError


# (正则, 提示文案)。顺序决定同一行命中多条时取哪条。
RULES: list[tuple[str, str]] = [
    (r"\beval\(|\bexec\(|os\.system", "动态执行不可信输入有代码注入风险，改用白名单或参数化调用"),
    (r"\bexcept\s*:", "裸 except 会吞掉所有异常（含 KeyboardInterrupt），请写明异常类型"),
    (r"\bexcept\s+Exception\b", "捕获 Exception 范围过宽，建议只捕获预期异常"),
    (r"while\s+True", "while True 没有退出条件，需要加超时或哨兵值"),
    (r"time\.sleep\(", "同步 sleep 会阻塞调用线程，考虑异步等待或事件通知"),
    (r"==\s*None|!=\s*None", "和 None 比较请用 is / is not"),
    (r"TODO|FIXME", "遗留待办，合并前需要处理或转成 issue"),
]

COMPILED_RULES = [(re.compile(pattern), message) for pattern, message in RULES]

MAX_COMMENTS = 8


class MockReviewer(Reviewer):
    """确定性规则 Reviewer：同样的 diff 永远得到同样的意见。"""

    name = "mock"

    def review(self, pr: dict[str, Any]) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []

        for row in parse_added(pr.get("diff", "")):
            for pattern, message in COMPILED_RULES:
                if pattern.search(row["text"]):
                    comments.append({
                        "file": row["file"],
                        "line": row["line"],
                        "body": message,
                    })
                    break
            if len(comments) >= MAX_COMMENTS:
                break

        return comments


PROMPT_TEMPLATE = """你是一名严格的代码评审者。请审阅下面的 PR 改动，指出真实存在的问题。

只输出 JSON 数组，不要任何解释文字。每个元素形如：
{{"file": "路径", "line": 行号, "body": "问题描述"}}

如果没有发现问题，输出 []。

PR 标题：{title}
仓库：{repo}

diff：
{diff}
"""


class OpenAICompatReviewer(Reviewer):
    """通过 OpenAI 兼容的 ``/chat/completions`` 调用真实模型。"""

    name = "openai"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        model = model or os.environ.get("OPENAI_MODEL")

        missing = [
            name
            for name, value in (
                ("OPENAI_BASE_URL", base_url),
                ("OPENAI_API_KEY", api_key),
                ("OPENAI_MODEL", model),
            )
            if not value
        ]
        if missing:
            raise ReviewerError(
                "缺少环境变量：" + "、".join(missing) + "。设置后再用 --reviewer openai。"
            )

        base = str(base_url).rstrip("/")
        self.endpoint = (
            base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        )
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def _call(self, prompt: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }).encode("utf-8")

        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ReviewerError(f"调用模型失败：{exc}") from exc

        try:
            return body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ReviewerError("模型响应缺少 choices[0].message.content") from exc

    def review(self, pr: dict[str, Any]) -> list[dict[str, Any]]:
        prompt = PROMPT_TEMPLATE.format(
            title=pr.get("title", ""),
            repo=pr.get("repo", ""),
            diff=pr.get("diff", ""),
        )
        raw = self._call(prompt)

        # 模型有时会包一层 ```json，剥掉。
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []

        if not isinstance(parsed, list):
            return []

        comments = []
        for item in parsed[:MAX_COMMENTS]:
            if not isinstance(item, dict):
                continue
            comments.append({
                "file": str(item.get("file", "")),
                "line": int(item.get("line", 0) or 0),
                "body": str(item.get("body", "")),
            })
        return comments


def build_reviewer(name: str) -> Reviewer:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockReviewer()
    if normalized == "openai":
        return OpenAICompatReviewer()
    raise ReviewerError(f"未知 reviewer：{name}（可选 mock / openai）")


__all__ = ["Reviewer", "ReviewerError", "MockReviewer", "OpenAICompatReviewer", "build_reviewer"]
