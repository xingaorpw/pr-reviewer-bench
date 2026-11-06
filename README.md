# pr-reviewer-bench

> 给 AI code review 打分：用带人工评审意见的真实 PR 做基准，量化它到底能不能发现问题。

[![tests](https://github.com/xingaorpw/pr-reviewer-bench/actions/workflows/ci.yml/badge.svg)](https://github.com/xingaorpw/pr-reviewer-bench/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)

---

## 它解决什么问题

"AI 能 review 代码"现在是**感觉**，不是数据。

你换了模型、改了提示词，感觉它变好了——但它真的多发现了几个 bug？还是只是话变多了？

这个项目把"review 得好不好"拆成三个能算的东西，用**带人工评审意见的真实 PR** 当基准，给出可复现的分数。

### 真实输出

```
$ python -m bench run --dataset data/seed.jsonl --reviewer mock --out r.json
pr-reviewer-bench · reviewer=mock · data/seed.jsonl

  pr-001     分数 0.00   文件 0.00   关键词 0.00   精准 0.00   0 条意见
  pr-002     分数 0.00   文件 0.00   关键词 0.00   精准 0.00   0 条意见
  pr-003     分数 0.82   文件 1.00   关键词 0.55   精准 1.00   2 条意见
  pr-004     分数 0.80   文件 1.00   关键词 0.50   精准 1.00   1 条意见
  pr-005     分数 0.84   文件 1.00   关键词 0.60   精准 1.00   3 条意见

  平均分 0.492
```

```
$ python -m bench report r.json
  reviewer      mock
  PR 数         5
  平均分        0.492

  分项指标
    file_recall      0.600  ████████████
    keyword_recall   0.331  ███████
    precision_proxy  0.600  ████████████

  最弱的一项：keyword_recall（0.331）
```

这个结果本身就有信息量：`mock` 是纯正则规则，它**完全漏掉了前两个 PR**（缓存竞态和 N+1 查询）——因为这两个问题没有任何关键字特征。这正是基准该暴露的东西。

---

## 快速开始

零依赖，只用标准库：

```bash
python -m bench run --dataset data/seed.jsonl --reviewer mock --out r.json
python -m bench report r.json
```

接真实模型：

```bash
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4o

python -m bench run --dataset data/seed.jsonl --reviewer openai --out gpt4o.json
python -m bench report gpt4o.json
```

没配环境变量时 `--reviewer openai` 会直接报错退出，不会静默返回空结果。

---

## 指标怎么算

三个指标，权重加起来是 1。

### `file_recall`（权重 0.4）

人工评审提到了哪些文件，reviewer 有没有覆盖到同一个文件。

```
命中文件数 / 人工提到的文件数
```

最宽松的一层：至少它看对了地方。

### `keyword_recall`（权重 0.4）

对每条人工意见，抽关键词（英文取 3 字符以上的词并去停用词，**中文切 bigram**），看 reviewer 的输出里命中了多少比例，再对所有人工意见取平均。

用 bigram 是因为：不切的话「比较」和「比较请用」会被当成两个不同的词，永远匹配不上。

### `precision_proxy`（权重 0.2）

reviewer 提的每条意见，`(file, line)` 是否落在 diff 的**新增行**范围内。

落在范围内算有效，落在被删除的行、没改的行、或者根本不存在的文件上都算噪声。

叫 proxy 是因为它不是真正的"正确率"——**行内位置对不代表说得对**，只是"没有瞎指"。

### 总分

```
score = 0.4 × file_recall + 0.4 × keyword_recall + 0.2 × precision_proxy
```

前两项权重高，因为"发现问题"比"指对位置"重要。

---

## 怎么加自己的数据

`data/seed.jsonl`，一行一个 JSON：

```json
{"id":"pr-001","repo":"demo/webapp","title":"fix: avoid duplicate cache eviction",
 "diff":"--- a/src/cache.py\n+++ b/src/cache.py\n@@ -10,9 +10,13 @@\n ...",
 "human_comments":[{"file":"src/cache.py","line":13,"body":"这里没加锁，两个线程会同时 fetch"}]}
```

- `#` 开头的行会被跳过（可以当注释）
- 空行会被跳过
- 缺字段会报**带行号**的错误，不会静默忽略

### 怎么造高质量数据

1. 从真实仓库里挑**已经合并**的 PR
2. 只保留**有实质评审意见**的（"LGTM"这种没有信息量）
3. diff 用 GitHub 上的 unified diff，不要自己手写
4. 人工意见的 `line` 要对准 diff 里的行号，否则 `precision_proxy` 会失真

种子数据只有 5 条，是手写的、教学性质的。**要当基准用，至少需要 50 条真实 PR。**

---

## 已知限制

说清楚，免得被误用：

- **seed 只有 5 条**，统计意义接近于零。它验证的是**流程**，不是模型能力排名。
- **`keyword_recall` 是代理指标**。模型换一种说法描述同一个问题，就算没命中——它惩罚的是"用词不同"，不是"说错了"。
- **不评估建议的正确性**。模型可能说"这里有 bug"而其实没有，`precision_proxy` 只检查位置，不检查对错。
- **不做跨文件推理的评估**。真实评审里"这个改动会破坏另一个模块"这类意见，当前指标完全测不到。
- **`MockReviewer` 是玩具**。它的存在是为了证明评测流程能跑通，不是用来当基线比性能的。

## Roadmap

- [ ] 扩充到 50+ 条真实 PR
- [ ] 加"是否复述 diff"的惩罚项（模型常见的偷懒行为）
- [ ] 支持多个 reviewer 同表对比
- [ ] 用 LLM 做语义匹配，替代关键词召回

## License

[MIT](LICENSE) © 2026 xingaorpw
