"""核心逻辑测试。跑法：python -m unittest discover -s tests -v"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench import cli, dataset, metrics  # noqa: E402
from bench.reviewers import (  # noqa: E402
    MockReviewer,
    OpenAICompatReviewer,
    ReviewerError,
)

SEED = Path(__file__).resolve().parent.parent / "data" / "seed.jsonl"

SIMPLE_PR = {
    "id": "pr-x",
    "repo": "demo/x",
    "title": "demo",
    "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,4 @@\n ctx\n+    if x == None:\n+        return []\n",
    "human_comments": [{"file": "a.py", "line": 2, "body": "用 is None 比较"}],
}


class TestDataset(unittest.TestCase):
    def test_load_seed(self):
        items = dataset.load(SEED)
        self.assertEqual(len(items), 5)
        self.assertEqual(items[0]["id"], "pr-001")
        for item in items:
            self.assertIn("human_comments", item)
            self.assertTrue(item["human_comments"])

    def test_comment_lines_are_skipped(self):
        items = dataset.load(SEED)
        self.assertTrue(all(isinstance(item, dict) for item in items))

    def test_missing_field_raises_with_lineno(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text('{"id":"a","repo":"b","title":"c","diff":"d"}\n', encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                dataset.load(path)
            self.assertIn(":1:", str(ctx.exception))
            self.assertIn("human_comments", str(ctx.exception))

    def test_bad_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            path.write_text("{not json}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                dataset.load(path)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            dataset.load(Path(tempfile.gettempdir()) / "definitely-not-here.jsonl")


class TestDiffParsing(unittest.TestCase):
    def test_added_lines_and_numbers(self):
        added = metrics.parse_added(SIMPLE_PR["diff"])
        self.assertEqual(len(added), 2)
        self.assertEqual(added[0]["line"], 2)
        self.assertIn("== None", added[0]["text"])

    def test_removed_lines_are_not_added(self):
        diff = "--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,1 @@\n-old\n-old2\n+new\n"
        added = metrics.parse_added(diff)
        self.assertEqual([row["text"] for row in added], ["new"])

    def test_multiple_files(self):
        diff = (
            "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+one\n"
            "--- a/b.py\n+++ b/b.py\n@@ -5,1 +5,2 @@\n ctx\n+two\n"
        )
        added = metrics.parse_added_lines(diff)
        self.assertEqual(sorted(added), ["a.py", "b.py"])
        self.assertEqual(added["b.py"], [6])


class TestMetrics(unittest.TestCase):
    def test_perfect_review_scores_one(self):
        pr = {
            "id": "p",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+    if x == None:\n",
            "human_comments": [{"file": "a.py", "line": 2, "body": "用 is None 比较"}],
        }
        comments = [{"file": "a.py", "line": 2, "body": "和 None 比较请用 is None"}]
        result = metrics.evaluate(pr, comments)
        self.assertAlmostEqual(result["score"], 1.0, places=4)

    def test_empty_review_scores_zero(self):
        pr = {
            "id": "p",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+    x = 1\n",
            "human_comments": [{"file": "a.py", "line": 2, "body": "这里有并发问题"}],
        }
        result = metrics.evaluate(pr, [])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["comments"], 0)

    def test_out_of_scope_comments_reduce_precision(self):
        pr = {
            "id": "p",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+    x = 1\n",
            "human_comments": [{"file": "a.py", "line": 2, "body": "命名不清楚"}],
        }
        comments = [{"file": "a.py", "line": 99, "body": "命名不清楚"}]
        result = metrics.evaluate(pr, comments)
        self.assertEqual(result["precision_proxy"], 0.0)

    def test_score_is_bounded(self):
        pr = {"id": "p", "diff": "", "human_comments": []}
        result = metrics.evaluate(pr, [{"file": "z", "line": 1, "body": "x"}])
        self.assertGreaterEqual(result["score"], 0.0)
        self.assertLessEqual(result["score"], 1.0)

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(metrics.WEIGHTS.values()), 1.0, places=6)


class TestReviewers(unittest.TestCase):
    def test_mock_flags_bare_except(self):
        pr = {
            "id": "p",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+    except:\n",
        }
        comments = MockReviewer().review(pr)
        self.assertEqual(len(comments), 1)
        self.assertIn("except", comments[0]["body"])

    def test_mock_flags_eval_and_none_compare(self):
        pr = {
            "id": "p",
            "diff": (
                "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,3 @@\n ctx\n"
                "+    return eval(x)\n+    if y == None:\n"
            ),
        }
        bodies = [c["body"] for c in MockReviewer().review(pr)]
        self.assertTrue(any("注入" in b for b in bodies))
        self.assertTrue(any("None" in b for b in bodies))

    def test_mock_is_deterministic(self):
        pr = {
            "id": "p",
            "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+    while True:\n",
        }
        first = MockReviewer().review(pr)
        second = MockReviewer().review(pr)
        self.assertEqual(first, second)

    def test_mock_respects_max_comments(self):
        lines = "".join("+    except:\n" for _ in range(20))
        pr = {"id": "p", "diff": f"--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,20 @@\n{lines}"}
        self.assertLessEqual(len(MockReviewer().review(pr)), 8)

    def test_mock_ignores_clean_diff(self):
        pr = {"id": "p", "diff": "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n ctx\n+    x = 1\n"}
        self.assertEqual(MockReviewer().review(pr), [])

    def test_openai_requires_env(self):
        import os

        keys = ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with self.assertRaises(ReviewerError):
                OpenAICompatReviewer()
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value


class TestCli(unittest.TestCase):
    def test_run_and_report_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "r.json")
            code = cli.main(["run", "--dataset", str(SEED), "--reviewer", "mock", "--out", out])
            self.assertEqual(code, 0)

            data = json.loads(Path(out).read_text(encoding="utf-8"))
            self.assertEqual(data["prs"], 5)
            self.assertEqual(data["reviewer"], "mock")
            self.assertGreater(data["average"], 0.2)
            self.assertLess(data["average"], 1.0)

            self.assertEqual(cli.main(["report", out]), 0)

    def test_missing_dataset_returns_1(self):
        self.assertEqual(
            cli.main(["run", "--dataset", "no-such-file.jsonl", "--reviewer", "mock"]),
            1,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
