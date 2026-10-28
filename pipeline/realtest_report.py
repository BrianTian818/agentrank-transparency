# -*- coding: utf-8 -*-
"""
RT-5 汇总出页：geo/rt5_runs.jsonl → rt5.html（服务端渲染）+ rt5.json

规则：
- 每个 (product, task) 取已跑次数的中位数；一次都没跑 → 标「未测」，不补 0、不推测
- RT-5 总分 = 五项中位数的等权平均（只算已测项），并在页面写明已测几项
- 三次原始分全部公开
"""
import datetime, io, json, os, statistics, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
RUNS = os.path.join(HERE, "rt5_runs.jsonl")

TASKS = ["T1", "T2", "T3", "T4", "T5"]
TASK_NAME = {"T1": "写代码", "T2": "做调研", "T3": "做表格",
             "T4": "做 PPT", "T5": "订行程"}

PRODUCT_NAME = {
    "perplexity": "Perplexity",
    "metaso": "秘塔 AI 搜索",
    "chatglm": "智谱清言",
}

# 未测原因：来自 _rt5_filter.py，只有「一次有效运行都没拿到」才算未测
UNUSABLE = os.path.join(HERE, "_rt5_unusable.json")


def load_unusable():
    if not os.path.exists(UNUSABLE):
        return {}
    try:
        return json.load(io.open(UNUSABLE, encoding="utf-8"))
    except Exception:
        return {}


COMMUNITY = os.path.join(HERE, "_rt5_community.json")


def load_community():
    """社区投稿（已通过人工审核）。

    分数不是投稿人填的，是我们的 grade_cli.py 算的 —— 与自测同一套脚本同一套归一化。
    """
    if not os.path.exists(COMMUNITY):
        return []
    try:
        j = json.load(io.open(COMMUNITY, encoding="utf-8"))
        return j.get("rows") or []
    except Exception:
        return []


CONTRIBUTORS = os.path.join(HERE, "_rt5_contributors.json")

# RT-5 覆盖度：候选产品「不登录能不能跑完一道真实任务」的匿名探测结果。
# 榜单上产品少不是因为我们懒 —— 是绝大多数产品把答案锁在登录后面。
# 这件事必须当数据公开，否则它会变成别人对我们的质疑。
COVERAGE = os.path.join(HERE, "_rt5_coverage_raw.json")

# 「跑了几题但没跑完」的产品：不是 0 分，是中位数口径不成立。
# 拿到的原文照样公开 —— 只公示「它被挡了」是不够的，得让访客看到我们到底拿到了什么。
BLOCKED = os.path.join(HERE, "_rt5_blocked.json")

RESULT_LABEL = {
    "answer":   ("匿名可跑通",          "#2E7D32", "不登录、不注册就能跑完任务，可进入 RT-5 实测"),
    "wall":     ("登录 / 注册墙",       "#B7791F", "投递后被要求登录或注册才能看到答案"),
    "no_input": ("找不到输入框",        "#B7791F", "首屏没有可投递任务的入口（多为营销站 / 需先进入工作台）"),
    "blocked":  ("反爬 / 人机验证拦截", "#C0392B", "被 Cloudflare 一类的人机验证挡住"),
    "no_answer": ("投递后未产出正文",   "#C0392B", "能投递但没拿到可判分的正文"),
    "unreachable": ("网络不可达",       "#C0392B", "从我们所在网络环境无法建立连接 —— 这是网络问题，不是产品问题"),
    "error":    ("访问异常",            "#C0392B", "页面加载或交互报错"),
    "skipped":  ("未探测",              "var(--ink-3)", "本轮未纳入探测"),
}
RESULT_ORDER = ["answer", "wall", "no_input", "blocked", "no_answer", "unreachable", "error", "skipped"]


def load_coverage():
    if not os.path.exists(COVERAGE):
        return []
    try:
        return json.load(io.open(COVERAGE, encoding="utf-8")).get("items") or []
    except Exception:
        return []


def load_blocked():
    if not os.path.exists(BLOCKED):
        return []
    try:
        return json.load(io.open(BLOCKED, encoding="utf-8")).get("items") or []
    except Exception:
        return []


def blocked_html(items):
    """测试受阻区块：跑了、但没跑完，所以没分。"""
    if not items:
        return ""
    rows = []
    for it in items:
        base = it.get("evidence_base") or "https://agentrank.xyz/rt5/evidence/"
        ok_links = "、".join(
            '<a href="%s%s.raw.txt" target="_blank" rel="noopener" '
            'style="color:var(--accent);font-weight:700">%s 原文</a>'
            % (base, rid, esc("T" + rid.split("-")[-2]))
            for rid in it.get("evidence_ok", []))
        wall = it.get("evidence_wall")
        wall_link = ('<a href="%s%s.png" target="_blank" rel="noopener" '
                     'style="color:var(--accent)">拦截截图</a>' % (base, wall)) if wall else "—"
        sc = it.get("computed_scores") or {}
        if sc:
            sc_txt = " / ".join("%s %s" % (k, sc[k]) for k in ("T1", "T2", "T3", "T4", "T5")
                                if k in sc)
            if it.get("scores_unreliable"):
                sc_cell = ('<span class="mono" style="font-weight:800">%s</span>'
                           '<div style="margin-top:6px;color:#C0392B;font-weight:700;'
                           'font-size:12.5px">⚠ 这几个数不能当成绩</div>'
                           '<div style="margin-top:4px;font-size:12px;color:var(--ink-3);'
                           'line-height:1.7">%s</div>'
                           % (esc(sc_txt), esc(it.get("why_scores_unreliable", ""))))
            else:
                sc_cell = '<span class="mono" style="font-weight:800">%s</span>' % esc(sc_txt)
        else:
            sc_cell = "—"
        rows.append(
            '<tr>'
            '<td style="font-weight:800">%s<div class="mono" style="font-size:11.5px;'
            'color:var(--ink-3);font-weight:400">%s</div></td>'
            '<td class="mono" style="text-align:center;font-weight:800">%s / %s</td>'
            '<td style="font-size:13px;color:var(--ink-2)">%s</td>'
            '<td style="font-size:13px;color:var(--ink-2)">%s</td>'
            '<td style="font-size:12.5px;max-width:300px">%s</td>'
            '<td style="font-size:12.5px">%s<div style="margin-top:6px;font-size:12px;'
            'color:var(--ink-3)">%s</div></td>'
            '</tr>'
            % (esc(it.get("label") or it.get("product")),
               esc((it.get("url") or "").replace("https://", "")),
               it.get("ok", 0), it.get("attempts", 0),
               esc(it.get("reason", "")),
               esc(it.get("ok_detail", "")),
               sc_cell, ok_links, wall_link))
    return """
<section class="section-tight" style="background:var(--bg-2);border-top:1px solid var(--line)">
  <div class="container">
    <div class="reveal">
      <span class="eyebrow">Blocked</span>
      <h2 class="section-title">跑了几题、但没跑完的（所以没分）</h2>
      <p style="font-size:14.5px;color:var(--ink-2);line-height:2;margin:0">
        RT-5 要求<strong>每题真跑 3 次取中位数</strong>。跑不满 3 次的题，中位数就不存在；
        只跑 1 次的题，那一次的好坏代表不了产品水平 —— 拿它当成绩就是撒谎。<br>
        所以这些产品<strong>不是 0 分，是没有分</strong>。我们已经拿到的正文和拦截截图全部公开，
        你可以自己看我们到底测到了什么。
      </p>
    </div>
    <div class="panel reveal" style="margin-top:24px;padding:0;overflow-x:auto">
      <table class="method-table">
        <tr><th>产品</th><th style="text-align:center">拿到正文 / 总投递</th>
            <th>中断原因</th><th>拿到了什么</th><th>跑出来的分</th><th>证据</th></tr>
        %s
      </table>
    </div>
    <p style="font-size:13px;color:var(--ink-3);line-height:1.9;margin:16px 0 0">
      这一栏存在的意义不是给这些产品打分，是把<strong>我们自己的采集缺陷</strong>也摆到台面上。
      分数算错了可以改，把「我们抓错了」说成「产品不行」不能改。
    </p>
  </div>
</section>
""" % ("\n        ".join(rows))


def coverage_html(items, board_n=0, board_ids=()):
    """覆盖度披露区块：把「我们试过谁、为什么没测成」摊开。"""
    if not items:
        return ""
    cnt = {}
    for it in items:
        cnt[it.get("result", "skipped")] = cnt.get(it.get("result", "skipped"), 0) + 1
    total = len(items)
    # 匿名可跑通、但还没跑完 RT-5 的产品 —— 必须点名，否则访客会问「为什么它不在榜上」
    pending = [it for it in items
               if it.get("result") == "answer" and it.get("name") not in board_ids]
    pending_line = ""
    if pending:
        names = "、".join(esc(it.get("label") or it.get("name")) for it in pending)
        pending_line = ('<br><strong>%s</strong> 匿名可以跑通，但还没跑完完整的 RT-5 '
                        '（匿名试用有频控，每天能跑的次数有限），已排进后续快照。'
                        % names)
    stats = []
    for k in RESULT_ORDER:
        if not cnt.get(k):
            continue
        lab, col, desc = RESULT_LABEL[k]
        stats.append(
            '<tr><td style="font-weight:700;color:%s">%s</td>'
            '<td class="mono" style="text-align:right;font-weight:800">%d</td>'
            '<td style="font-size:13px;color:var(--ink-3)">%s</td></tr>'
            % (col, esc(lab), cnt[k], esc(desc)))

    rows = []
    for it in sorted(items, key=lambda z: (RESULT_ORDER.index(z.get("result", "skipped")),
                                           z.get("label") or z.get("name"))):
        lab, col, _ = RESULT_LABEL.get(it.get("result", "skipped"), ("其它", "var(--ink-3)", ""))
        rows.append(
            '<tr><td class="mono" style="font-size:12px">%s</td>'
            '<td class="mono" style="font-size:12px;color:var(--ink-3)">%s</td>'
            '<td style="font-weight:700;color:%s;font-size:13px">%s</td>'
            '<td style="font-size:12px;color:var(--ink-3);max-width:420px">%s</td></tr>'
            % (esc(it.get("label") or it.get("name")), esc((it.get("url") or "").replace("https://", "")),
               col, esc(lab), esc(it.get("evidence") or "")))

    return """
<section class="section-tight" style="background:#fff;border-top:1px solid var(--line)">
  <div class="container">
    <div class="reveal">
      <span class="eyebrow">Coverage</span>
      <h2 class="section-title">为什么榜上只有 %d 个产品</h2>
      <p style="font-size:14.5px;color:var(--ink-2);line-height:2;margin:0">
        RT-5 的入选门槛是<strong>「不登录、不注册也能跑完一道真实任务」</strong>。我们对 %d 个候选产品
        用<strong>一次性匿名浏览器上下文</strong>（不复用任何登录态）投递了同一道探测题，结果如下。<br>
        被挡住的这些产品<strong>不是能力不行，是我们测不了</strong> —— 测不了的我们不打分、也不进榜，
        只把拦截证据写在这里。如果你有这些产品的付费账号，欢迎用
        <a href="/submit" style="color:var(--accent)">投稿通道</a>把真实运行结果发给我们。%s<br>
        <strong style="color:#B7791F">口径已收紧</strong>：下表的「匿名可跑通」只说明<strong>能跑完第 1 题</strong>，
        不等于能跑完 RT-5。要进榜必须真正跑满 <strong>5 题 × 3 次</strong>。2026-10-28 复核智谱清言，
        它首次探测是通过的，但补全 15 次时 11 次撞上登录墙，因此已改判为「测不了」。
      </p>
    </div>
    <div class="panel reveal" style="margin-top:24px;padding:0;overflow-x:auto">
      <table class="method-table">
        <tr><th>探测结果</th><th style="text-align:right">产品数</th><th>含义</th></tr>
        %s
      </table>
    </div>
    <details class="reveal" style="margin-top:18px">
      <summary style="cursor:pointer;font-size:14px;font-weight:700;color:var(--ink-2)">
        逐个产品看拦截证据（%d 个）
      </summary>
      <div class="panel" style="margin-top:14px;padding:0;overflow-x:auto">
        <table class="method-table">
          <tr><th>产品</th><th>入口</th><th>结果</th><th>证据（页面原文截取）</th></tr>
          %s
        </table>
      </div>
    </details>
  </div>
</section>
""" % (board_n, total, pending_line, "\n        ".join(stats), total, "\n        ".join(rows))


def load_contributors():
    """贡献者榜：按投稿邮箱哈希聚合，邮箱永不对外"""
    if not os.path.exists(CONTRIBUTORS):
        return []
    try:
        return json.load(io.open(CONTRIBUTORS, encoding="utf-8")).get("contributors") or []
    except Exception:
        return []


def contributors_html(rows, subs):
    """贡献者榜。

    存在的意义不是发奖品，是让「谁在帮这个榜变准」这件事被看见 ——
    投稿人为了上榜会回来，这才把一次性投稿变成复访。
    """
    if not rows:
        return ""
    trs = []
    for i, c in enumerate(rows, 1):
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, '<span class="mono" style="color:var(--ink-3)">%d</span>' % i)
        prod = "、".join(esc(x) for x in (c.get("products") or [])[:3])
        if len(c.get("products") or []) > 3:
            prod += " 等 %d 个" % len(c["products"])
        trs.append(
            '<tr><td style="text-align:center;font-size:16px">%s</td>'
            '<td style="font-weight:700">%s</td>'
            '<td class="mono" style="text-align:right;font-weight:800;color:var(--seal)">%d</td>'
            '<td class="mono" style="text-align:right">%d</td>'
            '<td class="mono" style="text-align:right">%d</td>'
            '<td style="font-size:12px;color:var(--ink-3)">%s</td>'
            '<td class="mono" style="font-size:12px;color:var(--ink-3)">%s</td></tr>'
            % (medal, esc(c.get("name") or "匿名投稿人"), c.get("points") or 0,
               c.get("count") or 0, c.get("withEvidence") or 0,
               prod or "—", esc(c.get("first") or "")))
    return """
<section class="section-tight" style="background:#fff;border-top:1px solid var(--line)">
  <div class="container">
    <div class="reveal">
      <span class="eyebrow">Contributors</span>
      <h2 class="section-title">谁在帮这个榜变准</h2>
      <p class="section-sub">这些分数不是我们一家跑出来的。下面是提交过实测证据、并通过审核的人。
        积分只在「证据被采纳」时增加 —— 投得多不如投得实。</p>
    </div>
    <div class="panel reveal" style="margin-top:24px;padding:0;overflow-x:auto">
      <div style="padding:22px">
        <table class="method-table">
          <tr><th style="text-align:center;width:44px">#</th><th>贡献者</th>
              <th style="text-align:right">积分</th><th style="text-align:right">投稿</th>
              <th style="text-align:right">附截图</th><th>覆盖产品</th><th>首次投稿</th></tr>
          %s
        </table>
        <p style="font-size:13px;color:var(--ink-3);margin:0 0 18px;line-height:1.9">
          积分 = 每通过审核一条 +1，附带截图证据链再 +1。按投稿邮箱聚合，邮箱不会公开展示。
        </p>
      </div>
    </div>
  </div>
</section>
""" % ("\n          ".join(trs),)


def build_community(rows):
    """按 (产品, 任务) 汇总；同一格有多条投稿时取中位数，并保留全部原始分

    取中位数而不是取最新或取最高 —— 取最高等于把决定权交给投稿量最大的人，
    取最新等于让最后一个投稿的人说了算。中位数是唯一「一条投稿左右不了」的算法。
    """
    by = {}
    for r in rows:
        key = (r.get("product") or "?", r.get("task") or "?")
        by.setdefault(key, []).append(r)
    out = {}
    for (p, t), rs in by.items():
        scores = [x.get("score") for x in rs if x.get("score") is not None]
        out[(p, t)] = {
            "median": med(scores) if scores else None,
            "n": len(scores),
            "raw": scores,
            "tier": sorted({x.get("tier") or "?" for x in rs}),
            "items": sorted(rs, key=lambda x: x.get("ts") or ""),
        }
    return out


def load():
    if not os.path.exists(RUNS):
        return []
    out = []
    for line in io.open(RUNS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def med(v):
    return round(statistics.median(v), 2)


def build():
    runs = load()
    products = {}
    for r in runs:
        p = r["product"]
        products.setdefault(p, {"runs": []})["runs"].append(r)

    unus = load_unusable()
    rows = []
    for p, d in sorted(products.items(), key=lambda kv: -1):
        cells, tested, medians = {}, 0, []
        for t in TASKS:
            rs = sorted([x for x in d["runs"] if x["task"] == t],
                        key=lambda x: x["run_index"])
            if not rs:
                cells[t] = {"untested": (unus.get(p) or {}).get(t, "本轮未取得有效运行")}
                continue
            m = med([x["score"] for x in rs])
            cells[t] = {"median": m, "n": len(rs),
                        "raw": [x["score"] for x in rs],
                        "run_ids": [x["run_id"] for x in rs]}
            medians.append(m)
            tested += 1
        total = round(sum(medians) / len(medians), 1) if medians else None
        tier = sorted({x.get("account_tier", "?") for x in d["runs"]})
        rows.append({"product": p, "cells": cells, "tested": tested,
                     "total": total, "tier": "/".join(tier),
                     "n_runs": len(d["runs"])})
    rows.sort(key=lambda r: (-(r["total"] or -1)))
    return rows, runs


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def community_html(rows):
    """社区投稿区块：与自测分开呈现，避免把「我们跑的」和「别人投的」混为一谈"""
    if not rows:
        return ('<p style="font-size:14px;color:var(--ink-3);line-height:1.9">'
                '还没有通过审核的社区投稿。你可以在 <a href="/submit" style="color:var(--seal);'
                'font-weight:700">/submit</a> 提交你的实测结果 —— '
                '提交的是产品的原始输出，分数由我们的判分脚本自动算出。</p>')
    agg = build_community(rows)
    prods = sorted({p for (p, t) in agg}, key=lambda x: x)
    trs = []
    for p in prods:
        tds = []
        for t in TASKS:
            c = agg.get((p, t))
            if not c or c["median"] is None:
                tds.append('<td class="mono" style="text-align:center;color:var(--ink-3)">—</td>')
                continue
            col = "#C0392B" if c["median"] < 40 else ("#B7791F" if c["median"] < 70 else "#2E7D32")
            # 样本不足 3 条时打星：中位数还不稳固，不能当成这个产品的定论
            thin = c["n"] < 3
            tds.append('<td class="mono" style="text-align:right;font-weight:700;color:%s" '
                       'title="投稿 %d 条，原始分 %s%s">%s%s</td>'
                       % (col, c["n"], " / ".join(str(x) for x in c["raw"]),
                          "；样本不足 3 条，仅供参考" if thin else "",
                          c["median"],
                          '<sup style="color:var(--ink-3);font-weight:400">*</sup>' if thin else ""))
        n = sum(agg[(p, t)]["n"] for t in TASKS if (p, t) in agg)
        tier = "/".join(sorted({x for t in TASKS if (p, t) in agg for x in agg[(p, t)]["tier"]}))
        trs.append('<tr><td class="mono" style="font-weight:700">%s</td>'
                   '<td style="text-align:center"><span class="mono" style="font-size:12px">%s</span></td>'
                   '%s<td class="mono" style="text-align:right;color:var(--ink-3)">%d 条投稿</td></tr>'
                   % (esc(p), esc(tier), "".join(tds), n))
    ev = []
    for r in sorted(rows, key=lambda x: (x.get("product") or "", x.get("task") or "", x.get("ts") or "")):
        jd = r.get("judge_detail") or {}
        detail = jd.get("reason") or (
            "%s/%s 通过" % (jd.get("passed"), jd.get("total")) if jd.get("passed") is not None
            else json.dumps({k: v for k, v in jd.items() if k not in ("urls", "detail")},
                            ensure_ascii=False)[:90])
        ev.append('<tr><td class="mono" style="font-weight:700">%s</td><td>%s</td>'
                  '<td class="mono" style="text-align:right;font-weight:700">%s</td>'
                  '<td style="font-size:12px;color:var(--ink-3)">%s</td>'
                  '<td class="mono" style="font-size:12px;color:var(--ink-3)">%s</td>'
                  '<td><a href="%s" target="_blank" rel="noopener">原文</a></td>'
                  '<td class="mono" style="font-size:12px;color:var(--ink-3)">%s</td></tr>'
                  % (esc(r.get("product") or "?"), esc(TASK_NAME.get(r.get("task"), r.get("task"))),
                     r.get("score"), esc(detail),
                     (r.get("ts") or "")[:10],
                     esc(r.get("evidence") or "#"), esc(r.get("id") or "")))
    return """
      <table class="method-table" style="margin-bottom:10px">
        <tr><th>产品</th><th style="text-align:center">档位</th>
            <th style="text-align:right">写代码</th><th style="text-align:right">做调研</th>
            <th style="text-align:right">做表格</th><th style="text-align:right">做 PPT</th>
            <th style="text-align:right">订行程</th><th style="text-align:right">投稿数</th></tr>
        %s
      </table>
      <p style="font-size:13px;color:var(--ink-3);margin:0 0 18px;line-height:1.9">
        一格有多条投稿时取<strong>中位数</strong>（不取最高、不取最新 —— 否则一条投稿就能左右一个产品的分数）；
        鼠标悬停可看到每条的原始分。<sup>*</sup> 表示该格投稿不足 3 条，中位数还不稳固，仅供参考。
      </p>
      <table class="method-table">
        <tr><th>产品</th><th>任务</th><th style="text-align:right">得分</th>
            <th>判分明细</th><th>投稿日期</th><th>逐字原文</th><th>投稿编号</th></tr>
        %s
      </table>
    """ % ("\n        ".join(trs), "\n        ".join(ev))


def page(rows, runs, comm, contribs, cov=None, blk=None):
    today = datetime.date.today().isoformat()
    from realtest_grade import GRADER_VERSION
    # 采集器版本：同一次快照里混着两套采集器就等于成绩不可比，必须写出来
    cols = sorted({(x.get("collector") or "rt5-collect-0.2（未标注）") for x in runs})
    trs = []
    for r in rows:
        tds = []
        for t in TASKS:
            c = r["cells"][t]
            if c is None or c.get("untested"):
                why = (c or {}).get("untested", "本轮未取得有效运行")
                tds.append('<td class="mono" style="text-align:center;color:var(--ink-3)" '
                           'title="未测原因：%s">未测</td>' % esc(why))
            else:
                col = "#C0392B" if c["median"] < 40 else ("#B7791F" if c["median"] < 70 else "#2E7D32")
                tds.append('<td class="mono" style="text-align:right;font-weight:700;color:%s" '
                           'title="原始分 %s（%d 次）">%s</td>'
                           % (col, " / ".join(str(x) for x in c["raw"]), c["n"], c["median"]))
        total = "—" if r["total"] is None else "%s" % r["total"]
        trs.append(
            '<tr><td class="mono" style="font-weight:700">%s</td>'
            '<td style="text-align:center"><span class="mono" style="font-size:12px">%s</span></td>'
            '%s<td class="mono" style="text-align:right;font-weight:800">%s</td>'
            '<td class="mono" style="text-align:right;color:var(--ink-3)">%d 项已测 / %d 次运行</td></tr>'
            % (esc(PRODUCT_NAME.get(r["product"], r["product"])), esc(r["tier"]),
               "".join(tds), total, r["tested"], r["n_runs"]))

    if not trs:
        trs = ['<tr><td colspan="9" style="text-align:center;color:var(--ink-3);padding:40px">'
               '尚无真实运行记录。本页只发布真跑出来的结果，跑一次更新一次。</td></tr>']

    ev = []
    for x in sorted(runs, key=lambda z: (z["product"], z["task"], z["run_index"])):
        # 用 run_id（含日期）当证据文件名 —— 光用 product-task-rN 会撞名：
        # 10-26 和 10-28 两批都有一条 perplexity-T1-r1，会互相覆盖
        base = x.get("run_id") or "%s-%s-r%d" % (x["product"], x["task"], x["run_index"])
        jd = x.get("judge_detail") or {}
        detail = jd.get("reason") or (
            "%s/%s 通过" % (jd.get("passed"), jd.get("total"))
            if jd.get("passed") is not None else
            json.dumps({k: v for k, v in jd.items() if k not in ("urls", "detail")},
                       ensure_ascii=False)[:120])
        ev.append(
            '<tr><td class="mono" style="font-size:12px">%s</td>'
            '<td>%s</td>'
            '<td class="mono" style="text-align:right;font-weight:700">%s</td>'
            '<td><a href="/rt5/evidence/%s.raw.txt" target="_blank" rel="noopener">原文</a></td>'
            '<td><a href="/rt5/evidence/%s.png" target="_blank" rel="noopener">截图</a></td>'
            '<td class="mono" style="text-align:right;color:var(--ink-3)">%s</td>'
            '<td style="font-size:12px;color:var(--ink-3)">%s</td></tr>'
            % (esc(base), esc(TASK_NAME.get(x["task"], x["task"])), x["score"],
               esc(base), esc(base),
               ("%.0fs" % x["duration_sec"]) if x.get("duration_sec") else "—",
               esc(detail)))

    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RT-5 真实能力实测 · AgentRank — 只发真跑出来的成绩</title>
<meta name="description" content="AgentRank RT-5 真实能力实测：写代码 / 做调研 / 做表格 / 做 PPT / 订行程 五项标准化任务，每项跑 3 次取中位数，原始输出与判分明细全部公开。跑不到的标「未测」，不用推测值补齐。">
<link rel="canonical" href="https://agentrank.xyz/rt5">
<meta property="og:title" content="RT-5 真实能力实测 · AgentRank">
<meta property="og:description" content="五项标准化任务真跑，每项 3 次取中位数，原始输出全公开。跑不到就标未测。">
<meta property="og:url" content="https://agentrank.xyz/rt5">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="/assets/css/style.css">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/img/favicon-32.png">
<script>window.SA_ENDPOINT='/api/track'</script><script src="/sa/track.js" defer></script>
</head>
<body>
<nav class="nav" id="nav"></nav>

<header class="page-hero">
  <div class="container">
    <span class="eyebrow">RT-5 · Real Task Runs</span>
    <h1 class="section-title" style="font-size:clamp(32px,5vw,52px)">真实能力实测：只发真跑出来的成绩</h1>
    <p class="section-sub">五项标准化任务（写代码 / 做调研 / 做表格 / 做 PPT / 订行程），每个任务真跑 3 次取中位数。
      判分脚本与每一次的原始输出全部公开，你可以自己复算。<strong>跑不到的产品标「未测」，不用推测值补齐。</strong><br>
      <span class="mono" style="font-size:12.5px;color:var(--ink-3)">判分器版本 %s · 采集器版本 %s · 两者任一改动都会升版本号，版本间分数不可直接比较</span></p>
  </div>
</header>

<section class="section-tight">
  <div class="container">
    <div class="panel reveal" style="padding:0;overflow-x:auto">
      <table class="method-table">
        <tr>
          <th>产品</th><th style="text-align:center">账号档位</th>
          <th style="text-align:right">写代码</th><th style="text-align:right">做调研</th>
          <th style="text-align:right">做表格</th><th style="text-align:right">做 PPT</th>
          <th style="text-align:right">订行程</th>
          <th style="text-align:right">RT-5</th><th style="text-align:right">样本</th>
        </tr>
        %s
      </table>
    </div>
    <p style="font-size:13px;color:var(--ink-3);margin-top:14px">
      鼠标悬停任一格可看到该任务的 3 次原始分。RT-5 = 五项中位数的等权平均。
    </p>
  </div>
</section>

<section class="section-tight" style="background:#fff;border-top:1px solid var(--line)">
  <div class="container">
    <div class="reveal">
      <span class="eyebrow">Community</span>
      <h2 class="section-title">社区提交 · 已核验</h2>
      <p class="section-sub">这一块不是我们自己跑的，是用户提交、经我们人工审核后公开的。
        但分数仍然是<strong>我们的判分脚本算的</strong>——投稿人提交的是产品的原始输出，
        既不能自己填分数，我们也改不了。<a href="/submit" style="color:var(--seal);font-weight:700">
        提交你的实测结果 →</a></p>
    </div>
    <div class="panel reveal" style="margin-top:24px;padding:0;overflow-x:auto">
      <div style="padding:22px">
        %s
      </div>
    </div>
  </div>
</section>

%s

<section class="section-tight" style="background:#fff;border-top:1px solid var(--line)">
  <div class="container">
    <div class="reveal">
      <span class="eyebrow">Raw Evidence</span>
      <h2 class="section-title">每一次运行的原始输出，逐字公开</h2>
      <p class="section-sub">分数是我们算的，但原料是产品自己吐出来的。下面每一次运行都能点开看原文与运行时的页面截图，
        你可以自己核对我们有没有删改、有没有"给辛苦分"。</p>
    </div>
    <div class="panel reveal" style="margin-top:24px;padding:0;overflow-x:auto">
      <table class="method-table">
        <tr><th>运行</th><th>任务</th><th style="text-align:right">得分</th>
            <th>逐字原文</th><th>页面截图</th><th style="text-align:right">等待时长</th><th>判分明细</th></tr>
        %s
      </table>
    </div>
  </div>
</section>

%s
%s
<section class="section-tight" style="background:#fff;border-top:1px solid var(--line)">
  <div class="container">
    <div class="reveal">
      <span class="eyebrow">Limits</span>
      <h2 class="section-title">这份实测的局限，我们写在前面</h2>
    </div>
    <div class="panel reveal" style="margin-top:28px;padding:28px">
      <ul style="font-size:14.5px;color:var(--ink-2);line-height:2.1;padding-left:0;list-style:none">
        <li>⚠ <strong>免费额度下的成绩 ≠ 付费版成绩</strong>：额度、可用模型、并发上限可能不同，页面已标明每家的账号档位。</li>
        <li>⚠ <strong>单次运行有随机性</strong>：每个任务跑 3 次，公布中位数，同时公开 3 次原始分。</li>
        <li>⚠ <strong>耗时不参与排名</strong>：受网络、限流、排队影响太大，只做记录不做分。</li>
        <li>⚠ <strong>样本极小</strong>：只能说明「这一轮、这一任务」的表现，不代表产品整体能力。</li>
        <li>⚠ <strong>与 VI-100 不是一回事</strong>：VI-100 测公开信息可核验程度，RT-5 测真跑任务的完成质量，两者不合并。</li>
      </ul>
    </div>
    <div class="panel reveal" style="margin-top:20px;padding:24px;background:var(--bg-2)">
      <div style="font-size:14.5px;color:var(--ink-2);line-height:2">
        <strong style="font-size:15px">不相信我们的分数？那就自己算一遍。</strong><br>
        全部 %d 次运行的逐字原文、送进判分器的文本、页面截图，加上判分器与采集器源码，
        都在公开仓库里：
        <a href="https://github.com/BrianTian818/agentrank-transparency" target="_blank"
           rel="noopener" class="mono" style="color:var(--accent);font-weight:700">github.com/BrianTian818/agentrank-transparency</a><br>
        拿源码跑一遍原文，应该得到和这一页一模一样的分数。
        对不上就是我们的错 —— 欢迎开 issue，那正是这个仓库存在的理由。
      </div>
    </div>
  </div>
</section>

<footer class="footer" id="footer"></footer>
<script src="/assets/js/app.js"></script>
<script>boot("");</script>
</body>
</html>
""" % (GRADER_VERSION, esc(" / ".join(cols)), "\n        ".join(trs), community_html(comm),
       contributors_html(contribs, comm), "\n        ".join(ev),
       blocked_html(blk),
       coverage_html(cov, board_n=len(rows), board_ids={r["product"] for r in rows}),
       len(runs))


def main():
    rows, runs = build()
    comm = load_community()
    contribs = load_contributors()
    cov = load_coverage()
    blk = load_blocked()
    html = page(rows, runs, comm, contribs, cov, blk)
    p_html = os.path.join(SITE, "rt5.html")
    io.open(p_html, "w", encoding="utf-8", newline="").write(html)
    p_json = os.path.join(HERE, "rt5.json")
    io.open(p_json, "w", encoding="utf-8", newline="").write(
        json.dumps({"generated": datetime.date.today().isoformat(),
                    "rows": rows, "n_runs": len(runs)}, ensure_ascii=False, indent=2))
    print("页面 %s (%d B)" % (p_html, len(html.encode("utf-8"))))
    print("数据 %s (%d 条运行)" % (p_json, len(runs)))
    for r in rows:
        print("  %-12s RT5=%-6s 已测 %d 项" % (r["product"], r["total"], r["tested"]))
    print("社区投稿 %d 条（已通过审核）" % len(comm))
    print("贡献者 %d 人" % len(contribs))
    if cov:
        c = {}
        for it in cov:
            c[it.get("result", "skipped")] = c.get(it.get("result", "skipped"), 0) + 1
        print("覆盖度探测 %d 个产品：%s" % (len(cov),
              " / ".join("%s %d" % (RESULT_LABEL[k][0], v)
                         for k, v in sorted(c.items(), key=lambda kv: -kv[1]))))


if __name__ == "__main__":
    main()
