# -*- coding: utf-8 -*-
"""确定性 HTML → Markdown 转换（只为还原结构化标记，不润色、不补内容）。

用途：产品把回答渲染成 HTML 后，Markdown 的 `## 标题` / `- 要点` / 代码围栏
     这些标记在 innerText 里就没了。判分器按标记判「格式遵循」，
     直接用 innerText 会让所有产品一律 0 分 —— 那是我们的采集缺陷，不是产品的能力。
     本模块把回答区的 HTML 还原成 Markdown，转换规则固定、可复现。

原则：
- 只还原 HTML 里确定存在的结构（标题层级、列表项、代码块、表格）
- 不猜测、不补字；拿不到就返回空串，让上层回退到纯文本
"""
import re
from html.parser import HTMLParser

BLOCK = {"p", "div", "section", "br", "tr"}
HEAD = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
LIST = {"ul", "ol"}


class MD(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []           # 输出行片段
        self.stack = []         # 标签栈
        self.buf = ""
        self.skip = 0          # 跳过 <script>/<style>
        self.li_stack = []      # 列表嵌套深度 + 有序标记

    # ---------- 工具 ----------
    def _flush(self, end=""):
        t = self.buf.strip()
        self.buf = ""
        if not t:
            return
        li = [x for x in self.li_stack if x is not None]
        if self.li_stack and self.li_stack[-1] is not None:
            # 列表项内部：一行一条，缩进表示层级
            depth = len(self.li_stack) - 1
            self.out.append("  " * depth + "- " + t)
        else:
            self.out.append(t + end)

    def _cur(self):
        return self.stack[-1] if self.stack else ""

    # ---------- 事件 ----------
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag in HEAD:
            self._flush()
            self.stack.append(tag)
            return
        if tag == "li":
            self._flush()
            self.li_stack.append(True)
            self.stack.append(tag)
            return
        if tag in LIST:
            self._flush()
            self.stack.append(tag)
            return
        if tag == "pre":
            self._flush("\n")
            self.stack.append(tag)
            self.buf = ""
            return
        if tag == "code" and self._cur() == "pre":
            return
        if tag in ("td", "th"):
            self.buf += " " if self.buf else ""
            return
        if tag == "tr":
            self._flush()
            self.stack.append(tag)
            return
        if tag in BLOCK or tag == "br":
            self._flush("\n")
            if tag != "br":
                self.stack.append(tag)
            else:
                self.out.append("")
            return
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in HEAD:
            # 标题内容：把这一行转成「## 标题」
            t = self.buf.strip()
            self.buf = ""
            mark = HEAD[tag]
            # 取出尚未 flush 的正文（标题里可能有内联标签）
            body = t or (self.out.pop() if self.out and not self.out[-1].startswith("#") else "")
            if body:
                self.out.append("%s %s" % (mark, body))
            if tag in self.stack:
                self.stack.remove(tag)
            return
        if tag == "li":
            self._flush()
            if self.li_stack:
                self.li_stack.pop()
            if tag in self.stack:
                self.stack.remove(tag)
            return
        if tag in LIST:
            self._flush("\n")
            if tag in self.stack:
                self.stack.remove(tag)
            return
        if tag == "pre":
            t = self.buf.strip("\n")
            self.buf = ""
            self.out.append("```")
            if t:
                self.out.append(t)
            self.out.append("```")
            self.out.append("")
            if tag in self.stack:
                self.stack.remove(tag)
            return
        if tag == "code" and self._cur() == "pre":
            return
        if tag == "tr":
            # 行内单元格合成一行，用 | 分隔
            t = self.buf.strip()
            self.buf = ""
            if t:
                self.out.append(t)
            if tag in self.stack:
                self.stack.remove(tag)
            return
        if tag in BLOCK:
            self._flush("\n")
            if tag in self.stack:
                self.stack.remove(tag)
            return
        if tag in self.stack:
            self.stack.remove(tag)

    def handle_data(self, data):
        if self.skip:
            return
        if self._cur() in ("td", "th"):
            self.buf += data.strip()
        else:
            self.buf += data

    def result(self):
        self._flush()
        lines = [l.rstrip() for l in self.out]
        # 压掉连续空行
        out, blank = [], False
        for l in lines:
            if not l.strip():
                if blank:
                    continue
                blank = True
            else:
                blank = False
            out.append(l)
        return "\n".join(out).strip()


def html2md(html):
    if not html:
        return ""
    p = MD()
    try:
        p.feed(html)
        p.close()
    except Exception:
        return ""
    return p.result()


if __name__ == "__main__":
    import sys, io
    src = sys.argv[1]
    print(html2md(io.open(src, encoding="utf-8").read()))
