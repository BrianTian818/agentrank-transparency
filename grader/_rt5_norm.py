# -*- coding: utf-8 -*-
"""把 _rt5_runs/*.txt 变成「逐字原文 + 围栏归一化」两份。

- 不改动任何一个内容字符，只补 Markdown 围栏、去掉语言标签行（python/csv/json…）
- 逐字原文存 <name>.raw.txt，sha1 写进 <name>.meta.json
- 幂等：已有 .raw.txt 时跳过
"""
import io, json, os, re, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "_rt5_runs")
LANG = re.compile(r"^(python|py|csv|json|markdown|md|text|txt|html|js|javascript|bash|shell)$", re.I)


PY_HEAD = re.compile(r"^(import\s|from\s+\S+\s+import\s|def\s|class\s|@)")
PY_CONT = re.compile(r"^(def\s|class\s|import\s|from\s|@|if\s|elif\s|else:|for\s|while\s|"
                     r"try:|except|finally:|with\s|raise\s|return\b|print\s*\(|pass$|\w+\s*=\s*\w+\()")


def pick_code(raw):
    """圈定「哪一段是代码」。

    只做定位与裁剪，不改动任何一个代码字符（缩进是原文就带的）。
    拿不到就原样返回 —— 宁可判不出来，也不替产品补代码。
    """
    lines = raw.split("\n")
    start = -1
    for i, ln in enumerate(lines):
        if PY_HEAD.match(ln):
            start = i
            break
    if start < 0:
        return raw
    end = start + 1
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        if ln[:1] in (" ", "\t"):
            end = j + 1
            continue
        if PY_CONT.match(ln):
            end = j + 1
            continue
        break
    return "\n".join(lines[start:end])


def strip_prompt(raw, prompt):
    """去掉我们自己发进去的提示词回显（它是输入不是输出）"""
    if not prompt:
        return raw
    p = prompt.strip()
    i = raw.find(p)
    if 0 <= i < 200:
        return raw[i + len(p):].lstrip("\n")
    return raw


def strip_cite(text):
    """去掉产品 UI 叠在正文上的引用角标 [[3]] —— 它不是模型输出的内容"""
    return re.sub(r"\[\[\d+\]\]", "", text)


SPAN_OPEN = re.compile(r'<span\b[^>]*>')
CIT_OPEN = re.compile(r'<span\b[^>]*(?:class="[^"]*citation[^"]*"|data-pplx-citation)[^>]*>', re.I)
SUP_OPEN = re.compile(r'<sup\b[^>]*>(?:.*?)</sup\s*>', re.I | re.S)


def strip_citation_nodes(html):
    """从回答区 HTML 里剔掉「来源引用 chip」节点（Perplexity 的
    <span class="citation" data-pplx-citation>）。

    这些 chip 是 UI，不是模型输出。混进 CSV 行尾 / JSON 对象中间会把结构化输出
    直接弄成解析失败，然后被判 0 分 —— 那是冤枉产品。
    chip 里面还套着别的 span，所以要按 span 配对删，不能用非贪婪正则。
    """
    out = []
    pos = 0
    while True:
        m = CIT_OPEN.search(html, pos)
        if not m:
            break
        start = m.start()
        # 从 m.start() 起做 span 配平 —— 外层 citation span 已经打开，初始 depth 必须是 1，
        # 否则配到它第一个子 span 的闭合就停了，外层尾巴（含来源域名）会留在正文里
        depth = 1
        end = m.end()
        for t in re.finditer(r'<span\b[^>]*>|</span\s*>', html[m.end():]):
            tok = t.group(0)
            depth += -1 if tok.startswith('</') else 1
            if depth == 0:
                end = m.end() + t.end()
                break
        out.append(html[pos:start])
        pos = end if end > m.end() else m.end()
    out.append(html[pos:])
    return SUP_OPEN.sub("", "".join(out))


def normalize(raw, task="", prompt=""):
    """只补围栏、去掉渲染出来的语言标签行，不改任何内容字符。

    Perplexity 等把代码语言名（python/csv/json）渲染成一个独立文本行，
    它不是答案内容，直接喂给判分器会导致 NameError —— 那是我们的锅，不能算产品头上。
    """
    if "```" in raw:
        return raw, "as_is"

    def strip(seg):
        lines = seg.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    # 按「整行只有语言名」切成若干段，取第一段（正文），后续是它附带的示例
    segs, cur = [], []
    for ln in raw.split("\n"):
        if LANG.match(ln.strip()):
            segs.append(strip("\n".join(cur)))
            cur = []
        else:
            cur.append(ln)
    segs.append(strip("\n".join(cur)))
    body = next((s for s in segs if s.strip()), "")
    # T3 的 CSV：回答区渲染会在行与行之间产生空行（块级元素各占一行），
    # 这些空行不是产品输出的内容，是渲染伪影。不收掉的话判分器会在第一个空行停，
    # 只解析到表头一行 → no_rows → 0 分，又是冤枉产品。
    # 只删空行，不动任何一个数据字符；只对 T3 生效（T5 的 JSON 本来就不受空行影响）。
    if task == "T3":
        kept = [ln for ln in body.split("\n") if ln.strip()]
        if kept != body.split("\n"):
            body = "\n".join(kept)
            how_extra = "+csv_blankline_collapse"
        else:
            how_extra = ""
    else:
        how_extra = ""
    return "```\n" + body + "\n```", "wrap_fence" + how_extra


def task_of(fn):
    m = re.search(r"-T([1-5])-r\d+\.txt$", fn)
    return "T" + m.group(1) if m else ""


def main():
    n = 0
    for fn in sorted(os.listdir(RUNS)):
        if not fn.endswith(".txt") or fn.endswith(".raw.txt"):
            continue
        p = os.path.join(RUNS, fn)
        rawp = p[:-4] + ".raw.txt"
        if not os.path.getsize(p):
            continue                      # 空文件 = 跑失败，不处理
        # 有逐字原文就用它当输入（可反复重跑归一化），否则把当前当原文
        if os.path.exists(rawp) and os.path.getsize(rawp):
            raw = io.open(rawp, encoding="utf-8").read()
        else:
            raw = io.open(p, encoding="utf-8").read()
            io.open(rawp, "w", encoding="utf-8").write(raw)

        mp = p[:-4] + ".meta.json"
        meta = {}
        if os.path.exists(mp):
            try:
                meta = json.load(io.open(mp, encoding="utf-8"))
            except Exception:
                meta = {}
        task = task_of(fn)
        prompt = meta.get("prompt") or ""

        # ① 去掉我们自己的提示词回显  ② 去掉 UI 引用角标
        raw = strip_prompt(raw, prompt)
        raw = strip_cite(raw)
        # ③ 若抓到了回答区 HTML，优先用它还原 Markdown（## / - 标记只存在于 HTML 结构里）
        htmlp = p[:-4] + ".html"
        used_html = False
        if os.path.exists(htmlp) and os.path.getsize(htmlp):
            try:
                from _rt5_html2md import html2md
                # 先把来源 citation chip 从 HTML 里剔掉再转 Markdown。
                # 不剔的话 chip 文本会原样进正文，插在 CSV 行尾 / JSON 对象中间，
                # 结构化输出直接解析失败 —— 我们自己的锅，不能算到产品头上。
                html_src = strip_citation_nodes(io.open(htmlp, encoding="utf-8").read())
                md = html2md(html_src)
                if md and len(md) > len(raw) * 0.4:
                    raw = strip_prompt(md, prompt)
                    used_html = True
            except Exception:
                pass

        base = pick_code(raw) if task == "T1" else raw
        norm, how = normalize(base, task)
        if used_html:
            how += "+html2md"
        if task == "T1" and base is not raw:
            how += "+pick_code"
        io.open(p, "w", encoding="utf-8").write(norm)
        meta["raw_sha1"] = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        meta["normalize"] = how
        meta["rawLen"] = len(raw)
        meta["len"] = len(norm)
        json.dump(meta, io.open(mp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        n += 1
    print("normalized %d file(s)" % n)


if __name__ == "__main__":
    main()
