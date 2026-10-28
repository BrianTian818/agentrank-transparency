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
data/coverage/     覆盖度探测：27 个候选产品「不登录能不能跑」
data/vi100/        VI-100 快照数据
evidence/rt5/      每次运行的逐字原文 + 页面截图
```

---

## 怎么复算 RT-5

RT-5 的每一次运行都留了**逐字原文**（`evidence/rt5/<run_id>.raw.txt`）。
把它喂给判分器，你应该得到和 `data/rt5/rt5_runs.jsonl` 里一模一样的分数：

```bash
# 判某一次运行（以 Perplexity 的代码题为例）
python grader/realtest_grade.py --task T1 \
    --file evidence/rt5/2026-10-28-perplexity-T1-r4.raw.txt

# 结果里会带上 grader_version，对不上就是版本变了
```

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
