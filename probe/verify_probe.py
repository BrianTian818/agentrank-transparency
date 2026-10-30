# -*- coding: utf-8 -*-
"""AgentRank 可核验指数（Verifiability Index, VI-100）真实抓取器
每个信号必须留证据：探测到的 URL + HTTP 状态 + 抓取时间。抓不到就是 0 分，不猜、不补。
"""
import json, os, re, ssl, sys, time, urllib.request, urllib.error, gzip, io
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = "Mozilla/5.0 (compatible; AgentRank-Verifier/1.0; +https://agentrank.xyz/methodology)"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PRODUCTS = [
    ("manus",      "Manus",              "Monica",            "https://manus.im"),
    ("genspark",   "Genspark",           "Genspark AI",       "https://www.genspark.ai"),
    ("devin",      "Devin",              "Cognition",         "https://devin.ai"),
    ("flowith",    "Flowith",            "Flowith",           "https://flowith.io"),
    ("fellou",     "Fellou",             "Fellou",            "https://fellou.ai"),
    ("minimax",    "MiniMax Agent",      "MiniMax",           "https://agent.minimax.io"),
    ("kimi",       "Kimi OK Computer",   "Moonshot AI",       "https://kimi.com"),
    ("skywork",    "Skywork",            "Skywork AI",        "https://skywork.ai"),
    ("lutra",      "Lutra",              "Lutra AI",          "https://lutra.ai"),
    ("convergence","Convergence",        "Convergence AI",    "https://convergence.ai"),
    ("openmanus",  "OpenManus",          "FoundationAgents",  "https://github.com/FoundationAgents/OpenManus"),
    ("suna",       "Suna",               "Kortix AI",         "https://suna.so"),
]

# 已知仓库（只在官网能证实链接时才计分，这里仅作“候选”，命中需官网页面里出现该链接）
KNOWN_REPO = {
    "openmanus": "FoundationAgents/OpenManus",
    "suna": "kortix-ai/suna",
}

CANDIDATES = {
    "pricing":  ["/pricing", "/price", "/plans", "/#pricing"],
    "docs":     ["/docs", "/documentation", "/doc", "/guide", "/help", "/support"],
    "terms":    ["/terms", "/terms-of-service", "/tos", "/terms-and-conditions", "/legal/terms"],
    "privacy":  ["/privacy", "/privacy-policy", "/legal/privacy"],
    "refund":   ["/refund", "/refund-policy", "/refunds", "/billing", "/legal/refund"],
    "security": ["/security", "/trust", "/trust-center", "/compliance"],
    "contact":  ["/contact", "/contact-us", "/about", "/support"],
    "changelog":["/changelog", "/releases", "/updates", "/blog", "/news"],
}

PRICE_RE = re.compile(r"([$€¥£]\s?\d[\d,\.]*)|(\d[\d,\.]*\s?(USD|CNY|EUR|GBP))", re.I)
FREE_RE = re.compile(r"\b(free plan|free tier|免费|free forever|get started free|start free|试用)\b", re.I)

# ---------------------------------------------------------------------------
# 抓取失败原因分类
#
# 这是诚实护栏的一部分：抓不到 ≠ 产品差。
# 超时 / 隧道失败 / DNS 失败 是「我们抓不到」，403/429 是「站点拒绝自动化访问」，
# 两者都不能当成低分发布，只能标「核验受阻」并单列公示。
# 曾经因为没做这件事，ChatGPT / Gemini / AgentGPT / Open Interpreter 被算成 3 分进榜，
# 而真实原因是「抓取环境连不上」——这属于把我们的基础设施问题写成产品缺陷。
# ---------------------------------------------------------------------------
REASON = {
    "timeout":   ("抓取超时（15 秒内未响应）", "Fetch timed out (no response within 15s)"),
    "tunnel":    ("抓取环境无法建立到该站点的连接", "Crawler could not establish a connection to the site"),
    "dns":       ("域名无法解析", "Domain could not be resolved"),
    "ssl":       ("TLS 握手失败", "TLS handshake failed"),
    "refused":   ("连接被拒绝", "Connection refused"),
    "reset":     ("连接被对端重置", "Connection reset by peer"),
    "botblock":  ("官网拒绝自动化访问（HTTP 401/403，多为反爬）",
                  "Site rejects automated access (HTTP 401/403, usually bot protection)"),
    "ratelimit": ("官网触发限流（HTTP 429）", "Site rate-limited the crawler (HTTP 429)"),
    "notfound":  ("官网地址不存在（HTTP 404/410）", "Official URL not found (HTTP 404/410)"),
    "server":    ("官网服务端错误（HTTP 5xx）", "Official site server error (HTTP 5xx)"),
    "other":     ("抓取失败", "Fetch failed"),
}

# 「我们的抓取环境问题」与「站点主动拒绝」要分开讲，页面上措辞不同
ENV_KINDS = {"timeout", "tunnel", "dns", "ssl", "refused", "reset", "other"}


def classify_err(e):
    s = "%s: %s" % (type(e).__name__, e)
    low = s.lower()
    if "timed out" in low or "timeout" in low or isinstance(e, TimeoutError):
        return "timeout"
    reason = getattr(e, "reason", None)
    if reason is not None:
        r = str(reason).lower()
        if "tunnel connection failed" in r or "proxy" in r:
            return "tunnel"
        if "name or service not known" in r or "nodename nor servname" in r or "getaddrinfo" in r:
            return "dns"
        if "certificate" in r or "ssl" in r:
            return "ssl"
        if "connection refused" in r:
            return "refused"
        if "connection reset" in r or "closed" in r:
            return "reset"
        if "timed out" in r:
            return "timeout"
    if "tunnel" in low or "proxy" in low:
        return "tunnel"
    if "name or service not known" in low or "getaddrinfo" in low:
        return "dns"
    if "certificate" in low or "ssl" in low:
        return "ssl"
    if "refused" in low:
        return "refused"
    if "reset" in low or "closed" in low:
        return "reset"
    return "other"


def classify_status(code):
    if code in (401, 403):
        return "botblock"
    if code == 429:
        return "ratelimit"
    if code in (404, 410):
        return "notfound"
    if 500 <= code < 600:
        return "server"
    return "other"


def verdict(home):
    """首页抓取结果 -> (kind, 中文原因, 英文原因)。状态不为 200 一律核验受阻。"""
    code = home.get("status", 0)
    if code == 0 or not code:
        kind = home.get("errKind") or "other"
    else:
        kind = classify_status(code)
    zh, en = REASON.get(kind, REASON["other"])
    if code:
        zh = "%s（HTTP %s）" % (zh, code)
        en = "%s (HTTP %s)" % (en, code)
    return kind, zh, en


def fetch(url, timeout=15, method="GET"):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "gzip",
    })
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            raw = r.read(400000)
            if r.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.decompress(raw)
                except Exception:
                    pass
            body = raw.decode("utf-8", "ignore")
            return {"ok": True, "status": r.status, "url": r.geturl(),
                    "ms": int((time.time() - t0) * 1000), "body": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "url": url,
                "ms": int((time.time() - t0) * 1000), "body": "",
                "errKind": classify_status(e.code)}
    except Exception as e:
        return {"ok": False, "status": 0, "url": url,
                "ms": int((time.time() - t0) * 1000), "body": "",
                "err": "%s: %s" % (type(e).__name__, e)[:200],
                "errKind": classify_err(e)}


def probe_path(base, paths):
    """逐个候选路径探测，返回第一个 200 的"""
    for p in paths:
        u = base.rstrip("/") + p
        r = fetch(u)
        if r["ok"] and r["status"] == 200:
            r["evidence"] = u
            return r
    return None


def github_api(repo):
    try:
        r = fetch("https://api.github.com/repos/%s" % repo, timeout=20)
        if not r["ok"]:
            return None
        d = json.loads(r["body"])
        return {
            "repo": repo, "stars": d.get("stargazers_count", 0),
            "forks": d.get("forks_count", 0), "open_issues": d.get("open_issues_count", 0),
            "license": (d.get("license") or {}).get("spdx_id") or "",
            "pushed_at": d.get("pushed_at", ""), "created_at": d.get("created_at", ""),
            "archived": d.get("archived", False),
            "url": "https://github.com/%s" % repo,
        }
    except Exception:
        return None


def one(p):
    pid, name, vendor, site = p
    out = {"id": pid, "name": name, "vendor": vendor, "site": site, "fetchedAt": time.strftime("%Y-%m-%d")}

    home = fetch(site, timeout=12)
    out["home"] = {"url": home["url"], "status": home["status"], "ms": home["ms"],
                   "https": home["url"].startswith("https://")}
    # 用最终落地域名作为 base
    m = re.match(r"(https?://[^/]+)", home["url"] if home["ok"] else site)
    base = m.group(1) if m else site

    body = home.get("body", "")

    # 首页拿不到 200：判定为「核验受阻」，不进榜、不打分。
    # 这里的 0 只是「该信号未取到」的占位，绝不能被当成产品得分——
    # score_vi.py 看到 unverifiable 会整条跳过，页面上也只显示「核验受阻 + 原因」。
    if not home["ok"] or home.get("status") != 200:
        kind, zh, en = verdict(home)
        out["unreachable"] = True          # 旧键，保留兼容
        out["unverifiable"] = True         # score_vi.py 读这个键决定「不进榜」
        out["unverifiableKind"] = kind
        out["unverifiableReason"] = zh
        out["unverifiableReasonEn"] = en
        out["envIssue"] = kind in ENV_KINDS   # True = 我们的抓取环境问题，非站点行为
        out["pricing"] = {"publicPrice": False, "freeTier": False, "evidence": "", "snippet": ""}
        out["docs"] = {"exists": False, "evidence": ""}
        for key in ("terms", "privacy", "refund", "security"):
            out[key] = {"exists": False, "evidence": ""}
        out["contact"] = {"page": False, "email": False, "emailValue": "", "evidence": ""}
        out["changelog"] = {"exists": False, "evidence": ""}
        out["machineReadable"] = {"robots_txt": False, "sitemap_xml": False,
                                  "llms_txt": False, "jsonld": False}
        repo_slug = KNOWN_REPO.get(pid)
        out["oss"] = github_api(repo_slug) if repo_slug else None
        print("done(unreachable)", pid, flush=True)
        return out

    # --- 定价透明度 ---
    pricing = {"publicPrice": False, "freeTier": False, "evidence": "", "snippet": ""}
    pr = probe_path(base, CANDIDATES["pricing"])
    scan_body = body or ""
    scan_url = home["url"]
    if pr:
        scan_body = pr["body"]; scan_url = pr["evidence"]
    if scan_body:
        mm = PRICE_RE.findall(scan_body)
        # 要求价格附近出现计费周期词，避免把无关的“$5,000,000 融资”当价格
        prices = [x for x in re.finditer(PRICE_RE, scan_body)]
        period = re.compile(r"(per month|/mo\b|/month|每月|per seat|annually|/year|/yr)", re.I)
        hit = None
        for x in prices:
            win = scan_body[max(0, x.start() - 120):x.end() + 120]
            if period.search(win):
                hit = x.group(0).strip(); break
        if hit:
            pricing["publicPrice"] = True
            pricing["snippet"] = hit
            pricing["evidence"] = scan_url
    if FREE_RE.search(scan_body[:200000]):
        pricing["freeTier"] = True
        if not pricing["evidence"]:
            pricing["evidence"] = scan_url
    out["pricing"] = pricing

    # --- 文档 ---
    d = probe_path(base, CANDIDATES["docs"])
    out["docs"] = {"exists": bool(d), "evidence": d["evidence"] if d else ""}

    # --- 条款/合规 ---
    for key in ("terms", "privacy", "refund", "security"):
        r = probe_path(base, CANDIDATES[key])
        out[key] = {"exists": bool(r), "evidence": r["evidence"] if r else ""}

    # --- 联系渠道 ---
    c = probe_path(base, CANDIDATES["contact"])
    mail = re.search(r"[\w\.\-]+@[\w\-]+\.[\w\.]{2,}", body[:200000])
    out["contact"] = {"page": bool(c), "email": bool(mail),
                      "emailValue": (mail.group(0) if mail else ""),
                      "evidence": c["evidence"] if c else ""}

    # --- 信息新鲜度（changelog / blog） ---
    cl = probe_path(base, CANDIDATES["changelog"])
    out["changelog"] = {"exists": bool(cl), "evidence": cl["evidence"] if cl else ""}

    # --- 机器可读性 ---
    mr = {}
    for f in ("robots.txt", "sitemap.xml", "llms.txt"):
        r = fetch(base.rstrip("/") + "/" + f, timeout=8)
        mr[f.replace(".", "_")] = (r["ok"] and r["status"] == 200)
    mr["jsonld"] = bool(re.search(r'application/ld\+json', body[:300000]))
    out["machineReadable"] = mr

    # --- 开源 ---
    repo_slug = KNOWN_REPO.get(pid)
    if not repo_slug:
        m2 = re.search(r"github\.com/([A-Za-z0-9_\-\.]+/[A-Za-z0-9_\-\.]+)", body[:300000])
        if m2:
            repo_slug = m2.group(1).rstrip(".")
    if repo_slug:
        g = github_api(repo_slug)
        out["oss"] = g or {"repo": repo_slug, "error": "github api failed"}
    else:
        out["oss"] = None

    print("done", pid, flush=True)
    return out


HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "vi_products.json")


def load_products():
    """抓取名单。优先 vi_products.json（由 _vi_batch.py 从目录清洗生成），
    没有它时退回硬编码的 12 个。"""
    if os.path.exists(MANIFEST):
        try:
            m = json.load(open(MANIFEST, encoding="utf-8"))["items"]
            return [(x["id"], x["name"], x["vendor"], x["site"]) for x in m]
        except Exception as e:
            print("cannot read manifest:", e, flush=True)
    return PRODUCTS


def run_all(items, outp, workers=10, timeout_home=12):
    """并发跑批 + 断点续跑 + 增量落盘（跑挂了不丢结果）。"""
    res, done = [], set()
    if os.path.exists(outp):
        try:
            prev = json.load(open(outp, encoding="utf-8"))
            res = prev.get("items", [])
            done = {x["id"] for x in res}
            print("resume, already have", len(res), flush=True)
        except Exception as e:
            print("cannot resume:", e, flush=True)

    todo = [p for p in items if p[0] not in done]
    print("todo %d / total %d, workers=%d" % (len(todo), len(items), workers), flush=True)
    if not todo:
        return res

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, p): p for p in todo}
        n = 0
        for f in as_completed(futs):
            p = futs[f]
            try:
                res.append(f.result())
            except Exception as e:
                print("FAIL", p[0], type(e).__name__, e, flush=True)
                res.append({"id": p[0], "name": p[1], "vendor": p[2],
                            "site": p[3], "error": str(e)[:200],
                            "unverifiable": True, "unverifiableKind": "other",
                            "unverifiableReason": "抓取过程异常：%s" % type(e).__name__})
            n += 1
            if n % 5 == 0 or n == len(todo):
                json.dump({"fetchedAt": time.strftime("%Y-%m-%d"), "items": res},
                          open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
                print("SAVED %d/%d  %.0fs" % (len(res), len(items), time.time() - t0), flush=True)
    json.dump({"fetchedAt": time.strftime("%Y-%m-%d"), "items": res},
              open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return res


if __name__ == "__main__":
    outp = os.path.join(HERE, "verified_signals_2026-10.json")
    items = load_products()
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    res = run_all(items, outp, workers=w)
    ok = [r for r in res if not r.get("unverifiable") and not r.get("error")]
    bl = [r for r in res if r.get("unverifiable")]
    print("WROTE", outp, len(res), "| 可核验", len(ok), "| 核验受阻", len(bl))
    for r in bl:
        print("  受阻 %-26s %s" % (r.get("id"), r.get("unverifiableReason", "")))
