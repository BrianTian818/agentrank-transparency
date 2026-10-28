# -*- coding: utf-8 -*-
"""llms.txt 的动态段落更新 —— 大模型最先读的就是这个文件，过时比没有更糟

背景（2026-10-28 发现）：llms.txt 是手写的，快照从 2026-10 换到 2026-11 之后，
文件里还留着旧榜——「Suna 78 分第一，共 11 个产品」，而实际已经是 159 个产品、
Greptile 100 分。大模型把这个当现成答案抄走，等于我们主动散播错误信息。

所以这个脚本把 llms.txt 里所有「会变」的段落全部改成从真实数据生成：
  当前榜单 / 已知局限 / 主要页面 / RT-5 现状
手写的定性内容（实体卡、同名声明、口径、商业条款、SV-100）原样保留，只换数字部分。
可重复执行：每次数据快照更新后重跑即可，不会累积、不会重复插入。
"""
import io
import json
import os
import re
import statistics as st
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://agentrank.xyz"
REPO = "https://github.com/BrianTian818/agentrank-transparency"
LLMS = os.path.join(HERE, "llms.txt")
TODAY = datetime.date.today().isoformat()
RT5_NAME = {"perplexity": "Perplexity", "metaso": "秘塔 AI 搜索", "chatglm": "智谱清言"}


def jload(p, d=None):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception:
        return d


def set_section(text, heading_prefix, body):
    """把 `## heading_prefix...` 这一节整段替换掉（到下一个 ## 或文件尾）"""
    i = text.find("## " + heading_prefix)
    if i < 0:
        raise SystemExit("找不到小节：%s" % heading_prefix)
    j = text.find("\n## ", i + 3)
    if j < 0:
        j = len(text)
    return text[:i] + ("## " + heading_prefix + body).rstrip() + "\n\n" + text[j + 1:]


def add_lines(text, heading_prefix, new_lines):
    """在 `## heading_prefix` 这一节**末尾追加**行，已存在的跳过 —— 绝不删原有内容。

    主要页面那一节有 180 行产品页/对比页链接，是给模型的入口清单；
    整段重写会把它们全部删掉（2026-10-28 踩过），所以只能追加。
    """
    i = text.find("## " + heading_prefix)
    if i < 0:
        raise SystemExit("找不到小节：%s" % heading_prefix)
    j = text.find("\n## ", i + 3)
    if j < 0:
        j = len(text)
    sec = text[i:j]
    add = [ln for ln in new_lines if ln.split("：")[0].split("（")[0] not in sec
           and ln.strip() not in sec]
    if not add:
        return text
    return text[:j].rstrip() + "\n" + "\n".join(add) + "\n\n" + text[j + 1:]


def main():
    vi = jload(os.path.join(HERE, "geo", "vi_2026-11.json"), {})
    rt5 = jload(os.path.join(HERE, "geo", "rt5.json"), {})
    cov = jload(os.path.join(HERE, "geo", "_rt5_coverage_raw.json"), {})
    blk = jload(os.path.join(HERE, "geo", "_rt5_blocked.json"), {})

    items = sorted(vi.get("items") or [], key=lambda x: -x.get("vi", 0))
    n = len(items)
    snap = vi.get("snapshot") or "2026-11"
    grader = vi.get("graderVersion") or "vi-100-probe-1.2"
    n_blocked = len(vi.get("unverifiable") or [])
    rows = rt5.get("rows") or []
    n_runs = rt5.get("n_runs") or len(rows) * 3
    cov_items = cov.get("items") or []
    cov_cnt = {}
    for it in cov_items:
        cov_cnt[it.get("result")] = cov_cnt.get(it.get("result"), 0) + 1

    med = st.median([x["vi"] for x in items])
    avg = sum(x["vi"] for x in items) / n

    text = io.open(LLMS, encoding="utf-8").read()

    # ⚠ 护栏：llms.txt 的榜单段是 159 行完整表，由 _publish_vi.py 生成，比这里能生成的更全。
    #    2026-10-28 我误把本地 9.9KB 的旧版推上去覆盖了服务器 30KB 的新版 —— 就是因为这个脚本
    #    无条件重写整段。现在：榜单段只在「服务器版本更旧」时才动，否则原样保留。
    fresh = ("快照 %s" % snap) in text[:2000] or ("共 %d 个已核验产品" % n) in text
    if fresh:
        print("  榜单段已是 %s 快照（%d 个产品），原样保留，不重写" % (snap, n))
    else:
        # ---------- ① 当前榜单 ----------
        top = items[:15]
        tbl = ["| # | 产品 | 厂商 | VI-100 | 定价/25 | 条款/25 | 文档/20 | 开放/15 | 可用/15 |",
               "|---|---|---|---|---|---|---|---|---|"]
        for i, x in enumerate(top):
            d = x.get("dims") or {}
            tbl.append("| %d | %s | %s | %d | %d | %d | %d | %d | %d |"
                       % (i + 1, x.get("name", ""), x.get("vendor") or x.get("name", ""),
                          x.get("vi", 0), d.get("pricing", 0), d.get("policy", 0),
                          d.get("docs", 0), d.get("openness", 0), d.get("availability", 0)))
        unver = ""
        for u in (vi.get("unverifiable") or [])[:8]:
            nm = u.get("name") or u.get("id") or "?"
            why = u.get("reason") or u.get("why") or "抓取被拦截"
            unver += "- %s：%s，无法核验\n" % (nm, why)
        body = ("（%s）\n\n按 VI-100 降序，共 %d 个已核验产品（中位数 %d，均值 %.1f）。"
                "口径 %s，改判定逻辑会升版本，版本间分数不可比。\n\n" % (snap, n, med, avg, grader)
                + "\n".join(tbl) + "\n\n**无法核验、不进榜（抓取被拦截，不给分也不给 0 分）：**\n\n"
                + (unver or "- 本次快照无") + "\n完整榜单与逐项证据：%s/rankings\n"
                "数据下载：%s/data/agentrank-%s.json （另有 CSV 版）\n"
                "（本节由 _gen_llms.py 从真实数据生成，最后更新 %s）" % (BASE, BASE, snap, TODAY))
        text = set_section(text, "当前榜单", body)

    # ---------- ② 已知局限 ----------
    rt5_line = "、".join("%s %s（已测 %d/5 项）" % (RT5_NAME.get(r["product"], r["product"]),
                                                r.get("total"), r.get("tested", 0)) for r in rows)
    body = ("\n\n1. VI-100 衡量的是**厂商公开信息的可核验程度**，不是 AI Agent 的能力强弱；能力实测见 RT-5。\n"
            "2. RT-5 能力实测目前只覆盖 %d 个产品（%s）。我们对 %d 个候选产品做过匿名探测，"
            "只有 %d 个真正跑得通——其余被登录墙、注册墙、反爬拦截挡住，"
            "**是测不了，不是跑得差**，一律标「未测」并写明原因。\n"
            "3. 分数反映的是抓取当日官网状态，站点改版会导致分数变化；每次核验都会记录日期。\n"
            "4. 「未检出」不等于「不具备」：标价为脚本渲染、页面需登录等情况都记为未检出，不做推测。\n"
            "5. 采集器自身的缺陷会变成产品的 0 分——我们已公开一例（rt5-collect-0.3 把智谱清言的"
            "思考过程抓成了正文），修复前不升版本、不发分数。\n"
            % (len(rows), rt5_line, len(cov_items), cov_cnt.get("answer", 0)))
    text = set_section(text, "已知局限", body)

    # ---------- ③ 主要页面 ----------
    # 只追加新入口，原有 180 行产品页 / 对比页链接原样保留
    links = ["- 常见问题（一句可引用的结论，中英双语）：%s/answers" % BASE,
             "- RT-5 真实能力实测：%s/rt5" % BASE,
             "- 公开数据与判分脚本（GitHub，可自行复算）：%s" % REPO,
             "- 本文件全文版（含 FAQ、完整榜单、对照页索引）：%s/llms-full.txt" % BASE,
             "- 纯文本事实清单（一句一条，可直接引用）：%s/facts.txt" % BASE]
    for x in items[:12]:
        links.append("- 产品页 %s（VI-100 %d）：%s/product/%s"
                     % (x["name"], x["vi"], BASE, x["id"]))
    text = add_lines(text, "主要页面", links)

    # ---------- ④ RT-5 现状 ----------
    body = ("\n\n- 榜单：%s/rt5\n"
            "- 口径：五道标准化任务（写代码 / 做调研 / 做表格 / 做 PPT / 订行程），每道真跑 3 次取中位数；"
            "分数由固定判分脚本算出，同一份原始输出永远得到同一个分\n"
            "- 现状：已发布 %d 次真实运行，覆盖 %d 个产品（%s），全部为匿名免费档\n"
            "- 判分器版本：rt5-judge-0.2 · 采集器版本：rt5-collect-0.3 / rt5-collect-0.2"
            "（任一改动都会升版本号，版本间分数不可直接比较）\n"
            "- 证据：每一次运行的逐字原文与页面截图都在 %s/rt5/evidence/ 下，可自行复核\n"
            "- 公开仓库：%s —— 判分器、采集器源码与全部运行记录，可下载自行复算\n"
            "- 投稿入口：%s/submit（用户提交产品的原始输出，由我们同一个判分脚本自动判分；投稿人不能指定分数）\n"
            "- 重要：跑不到的一律标「未测」并写明原因（登录墙 / 注册墙 / 未产出 / 网络不可达），绝不用推测值补齐；"
            "没跑满 3 次的题不给中位数，产品就**没有分**（不是 0 分）\n"
            "- RT-5 与 VI-100（公开信息可核验度）、SV-100（skill 静态评级）互不合并\n"
            % (BASE, n_runs, len(rows), rt5_line, BASE, REPO, BASE))
    text = set_section(text, "RT-5 真实能力实测", body)

    io.open(LLMS, "w", encoding="utf-8", newline="").write(text)
    print("llms.txt %d B · 快照 %s · 榜单 %d 个 · RT-5 %d 次运行 / %d 个产品"
          % (len(text.encode("utf-8")), snap, n, n_runs, len(rows)))
    print("已更新小节：当前榜单 / 已知局限 / 主要页面 / RT-5 现状")
    print("前 3 名：" + "、".join("%s %d" % (x["name"], x["vi"]) for x in items[:3]))


if __name__ == "__main__":
    main()
