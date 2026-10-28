# AgentRank · 公开数据与判分脚本

[agentrank.xyz](https://agentrank.xyz) 的全部原始记录与判分脚本。

**这个仓库存在的唯一目的：让你能自己复算我们的每一个分数。**

如果你发现我们算错了、抓错了、或者把「我们的采集缺陷」记成了「产品的能力问题」——
请直接开 issue，那正是这个仓库的意义。

---

## 三条产品线

| | 测什么 | 不测什么 |
|---|---|---|
| **VI-100 可核验指数** | 厂商公开信息（定价 / 文档 / 条款 / 开放度）能不能被查证 | 产品能力强不强 |
| **RT-5 真实能力实测** | 五道标准化任务真跑三次，机器判分 | 厂商宣传口径 |
| **SV-100 Skills 静态评级** | skill 的供应链、权限、元数据风险 | 运行时行为 |

> ⚠️ **VI-100 不是能力跑分。** 它只回答一个问题：这家厂商把该公开的信息公开了吗。
> 分数低 = 信息不透明，不等于产品不好。

---

## 目录

```
method/            方法论 + 五道题的提示词原文（单一事实来源）
grader/            判分器 + 归一化（唯一实现，改逻辑必须升版本）
collector/         真实浏览器采集器
pipeline/          过滤 / 合并脚本
data/rt5/          每次运行的逐字原文、判分明细、汇总（append-only）
data/rt5/blocked_* 跑了几题但没跑完的产品：拿到什么、为什么中断、为什么不打分
data/coverage/     覆盖度探测：27 个候选产品「不登录能不能跑」
data/vi100/        VI-100 快照数据
evidence/rt5/      每次运行的逐字原文 + 送进判分器的文本 + 页面截图
```

---

## 怎么复算 RT-5

每一次运行留**两份**文本，缺一不可：

| 文件 | 是什么 |
|---|---|
| `evidence/rt5/<run_id>.raw.txt` | 页面上抓下来的**逐字原文**，未经任何处理 |
| `evidence/rt5/<run_id>.graded.txt` | 归一化之后**真正送进判分器**的那一份 |

复算请用 `.graded.txt`——它是判分器的真实输入：

```bash
# 判某一次运行（以 Perplexity 的代码题为例）
python grader/realtest_grade.py --task T1 \
    --file evidence/rt5/2026-10-28-perplexity-T1-r4.graded.txt

# 结果里会带上 grader_version，对不上就是版本变了
```

我们自测：30 次运行里 **29 次复算分数与公布值完全一致**。

### ⚠️ 那 1 次对不上是怎么回事

`2026-10-26-metaso-2-r2`（秘塔 T2 第 2 次）公布 0.0 分，用当前判分器复算会得到 100.0。

**这不是数据错误，是判分器版本差异。** 那次运行的 `grader_version` 是
`rt5-judge-0.1`，当时的判分器没有做 citation chip 剔除，正文里明明有 6 个 URL，
却被判成 `no_url: 回答中没有解析出任何 URL`。

我们**没有**回头把 0.1 口径下的历史分数改写成 0.2 的分数。改了就是篡改历史记录，
而且会让「版本间分数不可比」这条规矩形同虚设。所以：

- 复算任何一次运行前，**先看那行的 `grader_version`**；
- 想对所有运行做横向比较，请用 `data/rt5/rt5_runs.jsonl` 里**同一版本**的子集。

这条差异本身也说明了一件事：**采集器的缺陷会变成产品的 0 分**。
所以我们把判分器和采集器都做了版本号，改逻辑必须升版本。

`data/rt5/rt5_runs.jsonl` 每行一次运行，字段说明：

| 字段 | 含义 |
|---|---|
| `raw_output` | 产品输出的逐字原文，不截断 |
| `score` | 判分器给的分数（0–100） |
| `judge_detail` | 判分明细：哪一项通过、哪一项没过、为什么 |
| `collector` | **采集器版本** |
| `grader_version` | **判分器版本** |
| `account_tier` | `free` / `paid` —— 免费档的成绩不等于付费档 |
| `collected_at` | 采集时间 |

---

## 口径版本（改逻辑必须升版本，版本间分数不可比）

| 组件 | 当前版本 | 变更历史 |
|---|---|---|
| 判分器 | `rt5-judge-0.2` | 0.1 初版；0.2 修 T3 的 CSV 围栏收尾被当成第 6 行数据 |
| 采集器 | `rt5-collect-0.3` | 0.2 答案容器优先；0.3 题面特征收工 + citation chip 剔除 + 登录墙早退 |
| 题面 | `rt5-prompts-0.1` | 五道题的提示词原文，自测与社区投稿共用 |

**为什么采集器也要有版本号？** 因为采集器的缺陷会直接变成产品的 0 分。
举两个真实发生过的例子：

- Perplexity 的页面会把引用来源（如 `finance.ifeng`）以 UI 角标形式插在正文里，
  正好插在 CSV 行尾和 JSON 对象中间 → 结构化输出解析失败 → 判 0 分。
  这是**我们抽取的锅**，不是产品的锅。
- 整页正文长度会被侧栏和页脚撑住，光用「长度够不够」判断答案出没出来，
  两个方向都会判错：UI 骨架凑够长度被当成跑通，天生只有 140 字的 CSV 被当成没跑出来。

发现同类问题请开 issue，我们会升版本并重算。

---

## 诚实护栏（这个项目不能破的规矩）

1. **抓不到记 0，不推测、不插值。**
2. **跑不出来标「未测」并写明原因**，绝不给推测分。登录墙、注册墙、加载卡死是
   「产品没跑出来」，不是「产品跑得差」，把它记成 0 分是撒谎。
3. **空集通过 ≠ 已审计。**
4. **改判分逻辑必须升版本**，并在页面标注，版本间分数不可比。

`data/rt5/rt5.json` 里 `untested` 字段就是这些「未测」格子，带原因。

---

## 覆盖度：为什么榜上只有几个产品

RT-5 的入选门槛是「**不登录、不注册也能跑完一道真实任务**」。
我们对 27 个候选产品用一次性匿名浏览器上下文投了同一道探测题，结果在
`data/coverage/coverage_2026-10-28.json`。

被挡住的产品**不是能力不行，是我们测不了**。测不了就不打分、不进榜，只把拦截证据写下来。

**口径已收紧（2026-10-28）**：探测通过只说明「能跑完第 1 题」，不等于能跑完 RT-5。
要进榜必须真正跑满 5 题 × 3 次。当天复核智谱清言时它就是个反例——首次探测是通过的
（答案区确实出现了函数定义），但补全 15 次时 11 次撞上微信扫码登录墙，
只拿到 T2/T3/T4/T5 各 1 次正文，**没有任何一题跑够 3 次**，因此改判为「测不了」。
逐条记录见 `data/coverage/coverage_2026-10-28.json` 里该条目的 `recheck` 字段
（`first_result` 保留了改判前的原始判定，不覆盖历史）。

### 「不是 0 分，是没有分」

`data/rt5/blocked_*.json` 收录这类产品：跑了、但没跑完，所以中位数口径不成立。
**拿到的原文和截图照样全部公开**，你可以自己看我们到底测到了什么。

---

## 已知缺陷（我们自己的锅，也一并公开）

**`rt5-collect-0.3` 会把智谱清言的思考过程当成正文一起抓下来。**

抓到的 4 份正文开头都是「ChatGLM语音梦幻杰思考结束…我需要搜索最新数据…10 个来源…」——
答案区选择器命中了包着推理链的外层容器。后果是 T3 判 0（`no_header`）、
T4 判 0（`no_h2`）、T5 判 0（`json_error`）。

**这三个 0 分是我们采集器的缺陷，不是智谱清言写不出表格、PPT 或 JSON。**
所以它们没有进榜，`blocked_2026-10-28.json` 里也标了 `scores_unreliable: true`，
原文照旧公开（`evidence/rt5/2026-10-28-chatglm-*.raw.txt`）。

修复方向已写进 `collector` 的注释：0.4 需要剥离思考过程容器，并在重跑验证通过后才允许升版本。
**在智谱清言的登录墙解开、能重新跑满 15 次之前，我们不会把修复后的分数发出去——没验证过就等于没修。**

这条记录本身就是 RT-5 存在的理由之一：**采集器的缺陷会直接变成产品的 0 分**。
所以我们给判分器和采集器都做了版本号，改逻辑必须升版本、必须公示。

---

## VI-100 数据

`data/vi100/` 是 2026-11 快照（164 个产品页 / 211 个对比页 / 159 个已核验）。
探测口径 `vi-100-probe-1.2`（三步发现法：常规路径 → 首页链接发现 → sitemap 反查）。

---

## 许可

- **代码**（`grader/` `collector/` `pipeline/`）：MIT
- **数据**（`data/` `evidence/`）：CC BY 4.0 —— 随便用，注明来源即可

---

## 相关链接

- **本仓库**：<https://github.com/BrianTian818/agentrank-transparency>
- 站点：<https://agentrank.xyz>
- RT-5 实测页：<https://agentrank.xyz/rt5>
- VI-100 榜单：<https://agentrank.xyz/rankings>
- 方法论：<https://agentrank.xyz/methodology>
- 投稿通道：<https://agentrank.xyz/submit>

---

## AgentRank Transparency Data & Grader Scripts

Every raw record and judging script behind [agentrank.xyz](https://agentrank.xyz),
published so you can recompute every score yourself.

Three product lines: **VI-100** (verifiability of vendor-published information —
*not* a capability benchmark), **RT-5** (real task runs, 5 standardized tasks × 3 runs,
machine-graded), **SV-100** (static supply-chain/permission rating for agent skills).

Recompute any RT-5 run:

```bash
python grader/realtest_grade.py --task T1 \
    --file evidence/rt5/2026-10-28-perplexity-T1-r4.raw.txt
```

Honesty guardrails we do not break: unreachable data is recorded as 0, never guessed;
a product that cannot run is marked **untested with a reason**, never given a speculative
score; any change to grading logic bumps the version, and scores across versions are
not comparable.
