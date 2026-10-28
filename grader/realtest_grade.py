# -*- coding: utf-8 -*-
"""
RT-5 真实能力实测判分器 v0.1
输入：某次运行的 raw_output 文本（+ task / product / meta）
输出：score(0-100) + judge_detail（可复现：同一份 raw_output 永远得到同一个分）

设计原则：
- 纯离线判定，不联网（T2 的 HTTP 校验可选开启 --net）
- 判不了就标 0 并写明原因，绝不"给个辛苦分"
- 版本号写进结果，保证历史可追溯
"""
import argparse, csv, io, json, os, re, sys

# 0.2（2026-10-26）：修正 T3 —— normalize() 补的代码围栏收尾被当成第 6 行 CSV 数据，
# 导致 row_count_5 / population_int / price_numeric 三项错误判负，T3 最高只能拿 40 分。
# 修正后重跑全部 14 次已发布运行，分数一个都没变（已发布数据不含 T3）。
GRADER_VERSION = "rt5-judge-0.2"

# ---------------------------------------------------------------- T1 写代码
T1_PROMPT = ("写一个 Python 函数 normalize_phone(s)：把任意格式的中国手机号归一化成 "
             "11 位纯数字字符串，非法输入返回 None。只输出代码，不要解释，"
             "不要 Markdown 代码块以外的内容。")

T1_CASES = [
    # (输入, 期望输出)
    ("13800138000", "13800138000"),
    ("+86 138 0013 8000", "13800138000"),
    ("138-0013-8000", "13800138000"),
    ("+8613800138000", "13800138000"),
    (" 138 0013 8000 ", "13800138000"),
    ("0086-138-0013-8000", None),          # 0086 前缀视为非法
    ("1380013800", None),                  # 位数不足
    ("138001380001", None),                # 位数超出
    ("", None),                            # 空串
    ("abcdefghijk", None),                 # 非数字
    ("18812345678", "18812345678"),
    ("199 0000 1111", "19900001111"),
]


def _extract_code(text):
    """从回答里抽出可执行的 Python 代码块"""
    m = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if m:
        return max(m, key=len)
    # 没有代码块：若整段就是代码（含 def）也接受
    if re.search(r"^\s*def\s+normalize_phone", text, re.M):
        return text
    return None


def grade_t1(text, net=False):
    code = _extract_code(text)
    if not code:
        return 0.0, {"passed": 0, "total": len(T1_CASES), "failed": ["__all__"],
                     "reason": "parse_failed: 未找到可执行的 Python 代码块"}
    ns = {}
    try:
        exec(compile(code, "<agent_output>", "exec"), ns)
    except Exception as e:
        return 0.0, {"passed": 0, "total": len(T1_CASES), "failed": ["__all__"],
                     "reason": "exec_failed: %s: %s" % (type(e).__name__, str(e)[:120])}
    fn = ns.get("normalize_phone")
    if not callable(fn):
        return 0.0, {"passed": 0, "total": len(T1_CASES), "failed": ["__all__"],
                     "reason": "no_function: 未定义 normalize_phone"}
    passed, failed = 0, []
    for i, (arg, want) in enumerate(T1_CASES):
        try:
            got = fn(arg)
        except Exception:
            got = "<exception>"
        if got == want:
            passed += 1
        else:
            failed.append("case%d(got=%r,want=%r)" % (i, got, want))
    return round(passed * 100.0 / len(T1_CASES), 2), {
        "passed": passed, "total": len(T1_CASES), "failed": failed}


# ---------------------------------------------------------------- T2 做调研
URL_RE = re.compile(r"https?://[^\s，。）)>\"'\]\[]+")


def grade_t2(text, net=False):
    urls = []
    for u in URL_RE.findall(text):
        u = u.rstrip(".,;:!?、，。)")
        if u not in urls:
            urls.append(u)
    urls = urls[:6]
    if not urls:
        return 0.0, {"valid": 0, "need": 3, "urls": [],
                     "reason": "no_url: 回答中没有解析出任何 URL"}
    detail = []
    valid = 0
    for u in urls:
        ok = None
        if net:
            try:
                import urllib.request
                req = urllib.request.Request(u, method="HEAD",
                                             headers={"User-Agent": "AgentRank-RT5/0.1"})
                with urllib.request.urlopen(req, timeout=12) as r:
                    ok = 200 <= r.status < 400
            except Exception as e:
                ok = False
        else:
            ok = None  # 离线模式只做格式校验
        detail.append({"url": u, "reachable": ok})
        if ok is None:
            valid += 1  # 离线：格式合法即计入，但会在 reason 里注明
        elif ok:
            valid += 1
    score = round(min(valid, 3) * 100.0 / 3, 2)
    d = {"valid": min(valid, 3), "need": 3, "urls": detail}
    if not net:
        d["reason"] = "offline: 未做 HTTP 可达性校验，仅统计格式合法的 URL 数"
    return score, d


# ---------------------------------------------------------------- T3 做表格
def grade_t3(text, net=False):
    # 取出最像 CSV 的连续行块
    lines = [l.rstrip() for l in text.splitlines()]
    header_idx = -1
    for i, l in enumerate(lines):
        if re.match(r"^\s*city\s*,\s*province\s*,\s*population_2025\s*,\s*avg_housing_price\s*$",
                    l.strip(), re.I):
            header_idx = i
            break
    if header_idx < 0:
        return 0.0, {"parse_ok": False, "reason": "no_header: 未找到规定表头"}
    block = []
    for l in lines[header_idx:]:
        # normalize() 会给没有围栏的输出补上 ``` 收尾，那是我们的包装不是产品的数据。
        # 不加这一行，收尾围栏会被当成第 6 条数据：row_count_5 挂、人口/房价校验挂，
        # T3 这一列任何产品最高只能拿 40 分 —— 典型的「我们的锅算到产品头上」。
        if l.strip().startswith("```"):
            break
        if not l.strip():
            if block:
                break
            continue
        block.append(l)
    if len(block) < 2:
        return 0.0, {"parse_ok": False, "reason": "no_rows"}
    try:
        rows = list(csv.DictReader(io.StringIO("\n".join(block))))
    except Exception as e:
        return 0.0, {"parse_ok": False, "reason": "csv_error: %s" % str(e)[:120]}

    checks = {}
    checks["parse_ok"] = True
    want = ["city", "province", "population_2025", "avg_housing_price"]
    got = [k.strip().lower() for k in (rows[0].keys() if rows else [])]
    checks["header_exact"] = got == want
    checks["row_count_5"] = len(rows) == 5

    pop_ok = price_ok = True
    for r in rows:
        p = (r.get("population_2025") or "").strip().replace(",", "")
        c = (r.get("avg_housing_price") or "").strip().replace(",", "")
        if not re.match(r"^\d+$", p):
            pop_ok = False
        if not re.match(r"^\d+(\.\d+)?$", c):
            price_ok = False
    checks["population_int"] = pop_ok
    checks["price_numeric"] = price_ok

    pts = {"parse_ok": 20, "header_exact": 20, "row_count_5": 20,
           "population_int": 20, "price_numeric": 20}
    score = sum(v for k, v in pts.items() if checks.get(k))
    return float(score), {"parse_ok": True, "rows": len(rows), "checks": checks,
                          "points": {k: (v if checks.get(k) else 0) for k, v in pts.items()}}


# ---------------------------------------------------------------- T4 做 PPT
def grade_t4(text, net=False):
    lines = text.splitlines()
    pages, cur = [], None
    for l in lines:
        if re.match(r"^##\s+\S", l):
            cur = {"title": l.lstrip("# ").strip(), "bullets": 0}
            pages.append(cur)
        elif cur is not None and re.match(r"^\s*([-*+]|\d+\.)\s+\S", l):
            cur["bullets"] += 1
    n = len(pages)
    if n == 0:
        return 0.0, {"pages": 0, "reason": "no_h2: 未找到任何二级标题"}
    page_pts = 40.0 if n == 8 else max(0.0, 40.0 - abs(n - 8) * 5.0)
    ok_b = sum(1 for p in pages if p["bullets"] == 3)
    bullet_pts = round(ok_b * 60.0 / max(n, 8), 2)
    return round(page_pts + bullet_pts, 2), {
        "pages": n, "pages_with_3_bullets": ok_b,
        "page_points": page_pts, "bullet_points": bullet_pts,
        "detail": [{"title": p["title"][:40], "bullets": p["bullets"]} for p in pages[:12]]}


# ---------------------------------------------------------------- T5 订行程
def grade_t5(text, net=False):
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return 0.0, {"parse_ok": False, "reason": "no_json: 未找到 JSON 对象"}
    try:
        data = json.loads(m.group(0))
    except Exception as e:
        return 0.0, {"parse_ok": False, "reason": "json_error: %s" % str(e)[:120]}
    days = data.get("days")
    if not isinstance(days, list):
        return 0.0, {"parse_ok": True, "reason": "no_days_field"}

    checks = {"parse_ok": True, "three_days": len(days) == 3, "min2_spots": True,
              "day3_before_18": False, "budget_ok": False}
    total = 0.0
    for d in days:
        spots = d.get("spots") or []
        if len(spots) < 2:
            checks["min2_spots"] = False
        for s in spots:
            try:
                total += float(s.get("cost") or 0)
            except Exception:
                pass
        try:
            total += float(d.get("cost") or 0)
        except Exception:
            pass
    if len(days) >= 3:
        last = (days[2].get("spots") or [])
        if last:
            t = str(last[-1].get("time", ""))
            mm = re.match(r"^(\d{1,2}):(\d{2})$", t.strip())
            if mm:
                h, mi = int(mm.group(1)), int(mm.group(2))
                checks["day3_before_18"] = (h < 18) or (h == 18 and mi == 0)
    checks["budget_ok"] = total <= 6000 and total > 0

    pts = {"parse_ok": 20, "three_days": 20, "min2_spots": 20,
           "day3_before_18": 20, "budget_ok": 20}
    score = sum(v for k, v in pts.items() if checks.get(k))
    return float(score), {"parse_ok": True, "total_cost": round(total, 2), "checks": checks,
                          "points": {k: (v if checks.get(k) else 0) for k, v in pts.items()}}


GRADERS = {"T1": grade_t1, "T2": grade_t2, "T3": grade_t3, "T4": grade_t4, "T5": grade_t5}


def grade(task, raw_output, net=False):
    g = GRADERS.get(task)
    if not g:
        raise ValueError("unknown task %s" % task)
    score, detail = g(raw_output, net=net)
    return {"task": task, "score": score, "judge_detail": detail,
            "grader_version": GRADER_VERSION}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True, choices=sorted(GRADERS))
    ap.add_argument("--file", help="raw_output 文本文件")
    ap.add_argument("--stdin", action="store_true")
    ap.add_argument("--net", action="store_true", help="T2 开启 HTTP 可达性校验")
    a = ap.parse_args()
    if a.stdin:
        txt = sys.stdin.read()
    elif a.file:
        txt = io.open(a.file, encoding="utf-8").read()
    else:
        ap.error("需要 --file 或 --stdin")
    print(json.dumps(grade(a.task, txt, net=a.net), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
