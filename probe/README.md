# VI-100 探测器（`vi-100-probe-1.2`）

这一目录是 **VI-100 可核验指数**的完整判分链路，对应快照 `2026-11`。
仓库口号是「每一个分数都能自己复算」——`data/vi100/` 给的是结果，这里给的是**怎么算出结果的**。

> ⚠️ VI-100 测的不是产品能力强不强，是**厂商公开信息能不能被查证**。
> 详见仓库根目录 `README.md` 与 <https://agentrank.xyz/methodology>。

## 三步发现法（为什么不是「猜路径」）

老版本只猜固定路径（`base + /pricing /docs /terms /privacy /refund /security /contact`），
于是 AutoGen（官方文档站 + GitHub 仓库分明都在）只得 15/100——它的文档压根不在这些路径上。
**路径猜不到 ≠ 厂商没公开**，所以探测器改成三步：

| 步骤 | 脚本 | 干什么 |
|---|---|---|
| 1 常规路径 | `_probe_all.js` | 按常见路径直接访问，最省事、能覆盖大多数产品 |
| 2 首页链接发现 | `_probe_links.js` | 抓首页渲染后的全部 `<a href>`，按 URL + 锚文本 + 子域名分类，**候选真机访问验证后才计入** |
| 3 sitemap 反查 | `_probe_sitemap.js` | 首页找不到时，去厂商自己的 sitemap 里反查（末段精确匹配，不能用首页那套模糊规则） |

效果：中位数 53 → 65，D 档 99 → 59 个，**没有一个产品分数下降**。

## 文件职责

| 文件 | 职责 |
|---|---|
| `merge_probe.py` | **判定的唯一实现** `judge()`。五维打分全在这里，不散落在任何别处 |
| `_probe_rules.js` | **链接分类规则的唯一实现**。两步发现共用这一份，避免「先撞见哪个」决定结论 |
| `_probe_common.js` | 共享访问层（访问 + 记录事实）。**不做判定** |
| `_probe_all.js` | 第 1 步，常规路径探测，支持并发与断点续跑 |
| `_probe_links.js` | 第 2 步，首页链接发现 + 候选验证 |
| `_probe_sitemap.js` | 第 3 步，sitemap 反查 |
| `_probe_machine.js` | 机器可读性探测（robots / sitemap / llms.txt / JSON-LD / 仓库线索），轻量不渲染 |
| `verify_probe.py` | 复算校验：用落盘数据重跑判分，比对是否一致 |

## 复算

```bash
python merge_probe.py \
  --browser  browser_probe.json \
  --urllib   urllib_probe.json \
  --products geo/vi_products.json \
  --ttfb     ttfb.json \
  --machine  machine_probe.json \
  --links    links_probe.json \
  --sitemap  sitemap_probe.json \
  --gh       gh_meta.json \
  --snapshot 2026-11 \
  --out      verified_signals_2026-11.json
```

`judge()` 的输入是**探测到的原始事实**（URL、状态码、正文、是否命中），
输出是五维分数与证据链接。改判分逻辑必须升 `GRADER_VERSION`（当前 `vi-100-probe-1.2`），
**不同版本的分数不可比**。

## 四条不打折的口径

1. **抓不到记 0，不推测不插值。** 探测失败就是失败，不给「大概应该有几分」。
2. **官网抓不到的产品判「核验受阻」，不给分也不记 0 分**，单独公示。
3. **候选必须过 `MIN_SCORE = 3` 门槛**（URL 命中 3 分 / 子域名命中 4 分，单靠锚文本 2 分不算）。
   不过门槛的话，"Accessibility" 会被当成条款页、营销指南会被当成文档。
4. **开源仓库必须校验归属。** 首页外链里的 GitHub 仓库可能是别人的——
   coderabbit 首页抓到 `tanstack/tanstack.com`、greptile 抓到 `nvidia/dali`，
   直接采信会把「开源」送给无关项目。校验见 `repo_belongs_to()`。

## 踩过的坑（改规则前必看）

- `blog/` 正则边界 bug：写 `blog\/(\/|$)` 会先吃掉斜杠，导致一条都过滤不掉 →
  必须写成 `blog(/|$|-|_|\.)`。
- gh 索引按原样大小写建，链接发现抓到的是小写 → 必须小写回查，否则许可证白丢 4 分。
- 营销页要排除：`/learn-more`、`/use-case/`、`/solutions/`、`/resources/guides/`，
  以及产品能力页里的 compliance 段落（例如某税务页）。
- sitemap 条目**没有锚文本佐证**，必须用 `classifyStrict()` 末段精确匹配；
  用首页那套模糊规则会大面积误判（术语表→安全页、白皮书→文档、
  退款 Agent 的产品页→退款政策、用户角色页→文档）。
- `_probe_links.js` 每产品 7 分钟看门狗；167 个产品 4 并发约 30 分钟。
  Edge/Chromium 首次导航常 TLS 冷启动超时，**必须重试一次**。
