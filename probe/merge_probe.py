# -*- coding: utf-8 -*-
"""把浏览器探测结果（主）与 urllib 探测结果（补充：GitHub API、机器可读性）合并成统一信号表。
关键防护：
1. SPA 假阳性 —— 子路径返回与首页相同的 title/正文/最终 URL，视为「页面不存在」
2. 抓取被拦截（403 / 连不上）—— 标记 unverifiable，不参与打分，不伪装成 0 分
"""
import json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
BROWSER = os.path.join(HERE, "browser_probe_2026-10.json")
URLLIB = os.path.join(HERE, "verified_signals_2026-10.json")
PRODUCTS = os.path.join(HERE, "products.json")
TTFB = os.path.join(HERE, "ttfb_2026-10.json")
OUT = os.path.join(HERE, "verified_signals_final.json")

# 路径 -> 信号名
MAP = {
    "/pricing": "pricing_page", "/docs": "docs", "/terms": "terms",
    "/privacy": "privacy", "/refund": "refund", "/security": "security",
    "/contact": "contact",
}

# 登录墙 / 跳转兜底：正文极小 且 最终 URL 与被请求 URL 不一致 —— 视为页面不存在
WALL_RE = re.compile(r"(auth|login|signin|sign-in|signup|onmicrosoft|redirect=)", re.I)
GH_REPO_RE = re.compile(r"^https?://(www\.)?github\.com/[^/]+/[^/]+/?$", re.I)
# 应用壳路径：子路由被塞回这些地址，说明该子页面并不存在
HOMEY = {"", "/", "/home", "/app", "/blank", "/index", "/dashboard", "/chat", "/home/", "/en", "/en/"}
# 定价页正向确认：标题含定价语义词，或页面上检出了具体标价
# 必须带词边界：否则 "Planners Apps" 会因为含 plan 被当成定价页（taskade 的 /apps/planners 踩过）
PRICE_TITLE_RE = re.compile(
    r"(\bpric\w*|\bplans?\b|\bsubscription\b|\bsubscriptions\b|\bsubscri\w+|\bpackages?\b"
    r"|\bbilling\b|套餐|定价|价格|订阅|付费|价目)", re.I)


def norm(u):
    return (u or "").rstrip("/").lower()


# 首页链接发现的分类名 -> 与猜路径统一的信号名
LINK_CAT = {
    "pricing": "pricing_page", "docs": "docs", "terms": "terms",
    "privacy": "privacy", "refund": "refund", "security": "security",
    "contact": "contact",
}


def judge(r, home_title, home_head, home_url, is_gh_repo, sig):
    """一条页面抓取记录是否真的对应「该产品的公开信息页」。

    猜路径（/pricing /docs …）和首页链接发现出来的候选共用这一份实现 ——
    判定标准若有两份，同一个产品就会因「先撞见哪个」而得到不同分数，
    那等于把自己的方法论变成随机数。
    """
    status = r.get("status", 0)
    raw_ok = 200 <= status < 400
    title = (r.get("title") or "").strip()
    # 正文很短也可能真实（内容 JS 懒加载 / 在 iframe 里），只要标题与首页不同就算真页面
    ok = raw_ok and (r.get("len", 0) >= 20 or bool(title and title != home_title))
    if r.get("notHtml"):
        ok = False
    # 软 404：未知路由被 SPA 塞回首页（标题 + 正文都一样）
    same_title = bool(home_title) and title == home_title
    same_head = bool(home_head) and (r.get("head") or "").strip()[:200] == home_head
    same_url = norm(r.get("finalUrl")) == home_url
    if (same_title and same_head) or same_url:
        ok = False
    # 登录墙 / 跳转墙：正文极小且被带到别的地址（典型 /terms -> /auth?redirect=...）
    req_url = norm(r.get("url"))
    fin_url = norm(r.get("finalUrl"))
    if ok and r.get("len", 0) < 400 and fin_url and fin_url != req_url:
        ok = False
    if ok and WALL_RE.search(r.get("finalUrl") or ""):
        ok = False
    # 被塞回应用壳（/home、/app、/blank …）—— 该子页面不存在
    if ok:
        fp = re.sub(r"^https?://[^/]+", "", fin_url or "").split("?")[0]
        if fp in HOMEY:
            ok = False
    # 加载失败的占位标题也算不存在
    if ok and re.match(r"^loading https?://", title, re.I):
        ok = False
    # GitHub 仓库没有产品级的定价/条款/隐私/退款/安全页；/security 只是漏洞上报入口。
    # 注意：开源项目的定价满分不来自这里——score_vi 对有仓库的项目另有「价格公开为 0」
    # 的兜底记分（见 score_vi 定价段）。这里挡的是「把代码托管页当成商业定价页」。
    if ok and is_gh_repo and sig in ("pricing_page", "terms", "privacy", "refund", "security"):
        ok = False
    # 定价页额外做正向确认，避免 SPA 把 /pricing 渲染成应用壳
    if ok and sig == "pricing_page":
        if not (PRICE_TITLE_RE.search(title or "") or (r.get("prices") or [])):
            ok = False
    return ok


def repo_belongs_to(repo, pid, meta):
    """首页外链里抓到的 GitHub 仓库是否真属于这个产品。

    首页常有赞助商 / 依赖 / 客户 logo 指向别人的仓库：实测 coderabbit 抓到
    tanstack/tanstack.com、greptile 抓到 nvidia/dali —— 直接采信会把「开源」
    这一项送给一个跟它毫无关系的项目，定价维度还会跟着走开源兜底拿满分。
    """
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", (s or "").lower())
    r = norm(repo)
    if not r or "/" not in repo:
        return False
    host = re.sub(r"^https?://", "", meta.get("site") or "")
    host = re.sub(r"^www\d*\.", "", host).split("/")[0].split(".")[0]
    cands = [norm(x) for x in (pid, meta.get("name", ""), meta.get("vendor", ""), host)]
    cands = [c for c in cands if len(c) >= 4]
    tail = norm(repo.split("/")[-1])
    return any(c in r or (tail and tail in c) for c in cands)


def parse_args():
    """做成可传参的通用版：扩量时只需要换输入文件，合并逻辑保持单一实现。
    不给参数时行为与 2026-10 那次完全一致（已发布的 12 条分数不受影响）。"""
    import argparse
    ap = argparse.ArgumentParser(description="合并浏览器探测与 urllib 探测为统一信号表")
    ap.add_argument("--browser", default=BROWSER)
    ap.add_argument("--urllib", default=URLLIB)
    ap.add_argument("--products", default=PRODUCTS)
    ap.add_argument("--ttfb", default=TTFB)
    ap.add_argument("--machine", default="", help="浏览器机器可读性探测结果（robots/sitemap/llms/JSON-LD/仓库）")
    ap.add_argument("--links", default="", help="首页链接发现探测结果（_probe_links.js 产出）")
    ap.add_argument("--sitemap", default="", help="sitemap 反查探测结果（_probe_sitemap.js 产出）")
    ap.add_argument("--gh", default="", help="GitHub 仓库元数据表 {slug: {...}}，由服务端调 GitHub API 产出")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--snapshot", default="2026-10-23")
    return ap.parse_args()


def main():
    a = parse_args()
    br = json.load(open(a.browser, encoding="utf-8"))
    ul = ({x["id"]: x for x in json.load(open(a.urllib, encoding="utf-8"))["items"]}
          if a.urllib and os.path.exists(a.urllib) else {})
    pm = {x["id"]: x for x in json.load(open(a.products, encoding="utf-8"))}
    tt = json.load(open(a.ttfb, encoding="utf-8")) if os.path.exists(a.ttfb) else {}
    mc = json.load(open(a.machine, encoding="utf-8")) if a.machine and os.path.exists(a.machine) else {}
    gh = json.load(open(a.gh, encoding="utf-8")) if a.gh and os.path.exists(a.gh) else {}
    gh_by_lower = {k.lower(): v for k, v in gh.items()}
    lkmap = json.load(open(a.links, encoding="utf-8")) if a.links and os.path.exists(a.links) else {}
    smmap = json.load(open(a.sitemap, encoding="utf-8")) if a.sitemap and os.path.exists(a.sitemap) else {}

    out = []
    REPO_REJECTED = []
    for pid, rec in br.items():
        if not rec.get("_done"):
            print("SKIP(incomplete)", pid)
            continue
        meta = pm.get(pid) or {}
        is_gh_repo = bool(GH_REPO_RE.match(rec["base"] or ""))
        home = rec["pages"].get("/", {})
        home_title = (home.get("title") or "").strip()
        home_head = (home.get("head") or "").strip()[:200]
        home_url = norm(home.get("finalUrl") or home.get("url"))

        item = {
            "id": pid, "base": rec["base"],
            "name": meta.get("name", pid),
            "vendor": meta.get("vendor", ""),
            "site": meta.get("site", rec["base"]),
            "logo": meta.get("logo", ""),
            "taglineZh": meta.get("taglineZh", ""),
            "taglineEn": meta.get("taglineEn", ""),
            "home": {
                "url": home.get("finalUrl") or home.get("url") or rec["base"],
                "status": home.get("status", 0),
                # 浏览器探测自己量到的真实 TTFB 优先（2026-11 起的探测才有）；
                # 量不到才退回独立的 TTFB 表。老数据 ms=0 会自然走回退分支。
                "ms": (home.get("ms") if (home.get("ms") or 0) > 0
                       else (tt.get(pid) or {}).get("ms", -1)),
                "https": (home.get("finalUrl") or rec["base"]).startswith("https://"),
            },
            "unverifiable": False,
            "unverifiableReason": "",
        }

        # ---- 抓取被拦截判定 ----
        # 这些一律「核验受阻」，不进榜、不打分——抓不到不等于产品不透明
        st = home.get("status", 0)
        if st == 0 and not home.get("ok"):
            item["unverifiable"] = True
            item["unverifiableReason"] = "官网无法连接（超时 / DNS / 被网络策略阻断）"
        elif st == 403 or st == 401:
            item["unverifiable"] = True
            item["unverifiableReason"] = "官网对自动化访问返回 %s，无法核验" % st
        elif st == 429:
            item["unverifiable"] = True
            item["unverifiableReason"] = "官网触发限流（429），无法核验"
        elif 500 <= st < 600:
            item["unverifiable"] = True
            item["unverifiableReason"] = "官网服务端错误（%s），无法核验" % st
        elif st == 200 and not home.get("ok") and home.get("len", 0) < 100:
            # 返回 200 但正文是空的：JS 空壳或反爬占位，后面的子路径探测全无意义
            item["unverifiable"] = True
            item["unverifiableReason"] = "首页返回 200 但正文为空（JS 空壳 / 反爬占位），无法核验"
        elif st != 200:
            # 兜底：只要拿不到干净的 200，就不能当成「产品信息不透明」去打分。
            # 实测踩到过 202 + 「Human Verification」这类人机验证页——
            # 它不属于上面任何一类，漏过去就会被当成一个正常站点算出分数。
            item["unverifiable"] = True
            item["unverifiableReason"] = "首页返回 HTTP %s（非 200，多为反爬或人机验证），无法核验" % st

        # ---- 子路径：SPA 假阳性防护 + 正文为空的误判修正 ----
        pages = {}
        for path, sig in MAP.items():
            r = rec["pages"].get(path, {})
            ok = judge(r, home_title, home_head, home_url, is_gh_repo, sig)
            pages[sig] = {"exists": ok, "evidence": (r.get("finalUrl") or r.get("url") or "") if ok else ""}

        # ---- 首页链接发现补位 ----
        # 猜路径漏掉的（文档在 /autogen/stable/、GitHub 链接在导航里…）用厂商自己写出来的
        # 真实链接补上。判定走同一个 judge()，只是记录来源不同——
        # 判定标准必须唯一，否则同一产品会出现两套结果。
        lk = (lkmap.get(pid) or {}) if lkmap else {}
        sm = (smmap.get(pid) or {}) if smmap else {}
        disc = {}
        for source in (lk, sm):
            for key, r in (source.get("visits") or {}).items():
                cat = LINK_CAT.get(r.get("cat"))
                if not cat:
                    continue
                if judge(r, home_title, home_head, home_url, is_gh_repo, cat):
                    disc.setdefault(cat, []).append(r)
        disc_used = []
        # 同一分类多个候选都通过时取分最高的：分数反映信号强度（子域名 > 路径 > 锚文本）
        for cat, hits in disc.items():
            hits.sort(key=lambda r: -(r.get("score") or 0))
            if pages[cat]["exists"]:
                continue                      # 猜路径已经拿到了，不重复记
            best = hits[0]
            pages[cat] = {"exists": True, "evidence": best.get("finalUrl") or best.get("url") or ""}
            disc_used.append(cat)
        item["_discovered"] = sorted(disc_used)

        # ---- 定价 ----
        price_hits = []
        src = rec["pages"].get("/pricing") or {}
        # 猜不到 /pricing 时，用「首页链接发现」验证过的真实定价页去取标价 / 免费档
        d_price = (disc.get("pricing_page") or [{}])[0]
        d_ok = bool(d_price)
        if src.get("ok") and src.get("prices"):
            price_hits = src["prices"]
        if not price_hits and d_ok and d_price.get("prices"):
            price_hits = d_price["prices"]
        if not price_hits and home.get("ok") and home.get("prices"):
            price_hits = home["prices"]
        free = bool((src.get("hasFree") if src.get("ok") else False)
                    or (d_price.get("hasFree") if d_ok else False)
                    or (home.get("hasFree") if home.get("ok") else False))
        price_ev = src.get("finalUrl") if src.get("ok") else ""
        if not price_ev and d_ok:
            price_ev = d_price.get("finalUrl") or d_price.get("url") or ""
        item["pricing"] = {
            "pageExists": bool(pages["pricing_page"]["exists"]),
            "publicPrice": bool(price_hits),
            "snippet": price_hits[0] if price_hits else "",
            "snippets": price_hits[:6],
            "freeTier": free,
            "evidence": price_ev or (home.get("finalUrl") or ""),
            "pageEvidence": pages["pricing_page"]["evidence"],
        }

        item["docs"] = pages["docs"]
        item["terms"] = pages["terms"]
        item["privacy"] = pages["privacy"]
        item["refund"] = pages["refund"]
        item["security"] = pages["security"]

        d_contact = (disc.get("contact") or [{}])[0]
        mail = bool((home.get("hasMail") if home.get("ok") else False)
                    or (src.get("hasMail") if src.get("ok") else False)
                    or (d_contact.get("hasMail") if d_contact else False))
        item["contact"] = {
            "page": pages["contact"]["exists"], "email": mail,
            "emailValue": "", "evidence": pages["contact"]["evidence"],
        }

        # ---- 机器可读性 ----
        # 优先用浏览器探测的结果：静态文件虽然不受 JS 影响，但 urllib 会被 Cloudflare 挡，
        # 「抓不到」记成「没有」会白白扣掉开放度分。浏览器网络栈拿得到真实答案。
        u = ul.get(pid, {})
        m0 = mc.get(pid) or {}
        item["machineReadable"] = m0.get("machineReadable") or u.get("machineReadable") or {
            "robots_txt": False, "sitemap_xml": False, "llms_txt": False, "jsonld": False}

        # ---- 开源 ----
        # GitHub 用户名/仓库名大小写不敏感，但 gh_meta 是按原样大小写建的索引。
        # 链接发现抓到的往往是小写（1panel-dev/maxkb），直接拿去查会查不到，
        # 退化成「只有仓库名、没有许可证」的空壳记录，把本来 15 分的开放度打到 11。
        item["oss"] = u.get("oss")
        for cand in (lk.get("repo"), m0.get("repo")):
            if item["oss"] or not cand:
                continue
            if cand == lk.get("repo") and not repo_belongs_to(cand, pid, meta):
                # 记下来但不采信：这类是首页外链里抓到的别人家的仓库
                REPO_REJECTED.append("%s -> %s" % (pid, cand))
                continue
            meta2 = gh.get(cand) or gh_by_lower.get(cand.lower())
            item["oss"] = meta2 or {"repo": cand, "url": "https://github.com/" + cand}
        if not item["oss"]:
            m = re.search(r"github\.com/([A-Za-z0-9_\-\.]+/[A-Za-z0-9_\-\.]+)", home.get("finalUrl") or "")
            if m:
                item["oss"] = {"repo": m.group(1).rstrip("."), "url": "https://github.com/" + m.group(1).rstrip(".")}

        item["fetchedAt"] = a.snapshot
        out.append(item)

    miss = [t["id"] for t in json.load(open(a.products, encoding="utf-8"))
            if t["id"] not in {x["id"] for x in out}]
    if miss:
        print("WARN 名单里有 %d 个没合并进来（浏览器探测未完成或缺失）：%s"
              % (len(miss), " ".join(miss[:20])))

    if REPO_REJECTED:
        print("WARN %d 个首页外链抓到的仓库与产品无关，未采信：%s"
              % (len(REPO_REJECTED), "; ".join(REPO_REJECTED)))

    json.dump({"fetchedAt": a.snapshot, "items": out},
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", a.out, len(out))
    blocked = [x for x in out if x["unverifiable"]]
    print("可核验 %d / 核验受阻 %d" % (len(out) - len(blocked), len(blocked)))
    for x in blocked:
        print("  受阻 %-24s %s" % (x["id"], x["unverifiableReason"]))
    for x in out:
        print("%-12s home=%-4s unverifiable=%-5s price=%-5s %-8s docs=%-5s terms=%-5s priv=%-5s"
              % (x["id"], x["home"]["status"], x["unverifiable"],
                 x["pricing"]["publicPrice"], repr(x["pricing"]["snippet"])[:8],
                 x["docs"]["exists"], x["terms"]["exists"], x["privacy"]["exists"]))


if __name__ == "__main__":
    main()
