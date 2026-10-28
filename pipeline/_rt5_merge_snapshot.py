# -*- coding: utf-8 -*-
"""RT-5 快照合并：把三批有效运行合成一份 rt5_runs.jsonl

批次与采集器版本：
  A  geo/rt5_runs_2026-10.jsonl            2026-10-26  rt5-collect-0.2
  B  geo/_rt5_runs_partial-1028/<选定文件>  2026-10-28  rt5-collect-0.2
     （只取未受「来源 chip 污染」影响的格子：秘塔 T3、Perplexity T4。
       Perplexity T3/T5 那两批被判分前作废 —— 我们的采集器把引用来源混进了
       CSV/JSON，那是我们的锅，作废重跑，不能算产品 0 分。）
  C  geo/_rt5_runs/                        2026-10-28  rt5-collect-0.3

run_index 按 (product, task) 内的 collected_at 顺序重新编号，保证唯一。
"""
import io, json, os, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from realtest_grade import grade, GRADER_VERSION  # noqa: E402
from realtest_ingest import make_record  # noqa: E402

OUT = os.path.join(HERE, "rt5_runs.jsonl")

A_PATH = os.path.join(HERE, "rt5_runs_2026-10.jsonl")
B_DIR = os.path.join(HERE, "_rt5_runs_partial-1028")
C_DIR = os.path.join(HERE, "_rt5_runs")
# B 批只取这些格子（其它格子已被 0.3 重新测过或作废）
B_KEEP = {"perplexity-T4", "metaso-T3"}


def load_a():
    recs = []
    for line in io.open(A_PATH, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        # 原始文件名：run_index 重编号后仍要能找回 .raw.txt / .png 证据
        r["src_stem"] = "%s-%s-r%d" % (r["product"], r["task"], r["run_index"])
        r["collector"] = r.get("collector") or "rt5-collect-0.2"
        r["collector_note"] = r.get("collector_note") or "答案容器优先抓取（早期版本）"
        recs.append(r)
    return recs


def load_dir(d, keep_prefixes=None, collector="", note=""):
    recs = []
    for f in sorted(glob.glob(os.path.join(d, "*.txt"))):
        base = os.path.basename(f)
        if base.endswith(".raw.txt"):
            continue
        if not os.path.getsize(f):
            continue
        stem = base[:-4]
        if keep_prefixes is not None and not any(stem.startswith(p) for p in keep_prefixes):
            continue
        mp = f[:-4] + ".meta.json"
        meta = {}
        if os.path.exists(mp):
            try:
                meta = json.load(io.open(mp, encoding="utf-8"))
            except Exception:
                meta = {}
        raw = io.open(f, encoding="utf-8").read()
        import re as _re
        m = _re.match(r"^(.+)-T([1-5])-r(\d+)$", stem)
        if not m:
            continue
        product, task = m.group(1), "T" + m.group(2)
        meta["collector"] = meta.get("collector") or collector
        meta["collector_note"] = meta.get("collector_note") or note
        rec = make_record(product, task, m.group(3), meta.get("account_tier", "free"),
                          raw, None, None, meta.get("note", ""), False, meta)
        rec["src_stem"] = stem
        rec["collector"] = meta["collector"]
        rec["collector_note"] = meta["collector_note"]
        recs.append(rec)
    return recs


def main():
    recs = load_a()
    n_a = len(recs)
    recs += load_dir(B_DIR, keep_prefixes=sorted(B_KEEP), collector="rt5-collect-0.2",
                     note="同 0.2 抽取逻辑（本轮仅收工条件不同，不影响已产出内容）")
    n_b = len(recs) - n_a
    recs += load_dir(C_DIR, keep_prefixes=None, collector="rt5-collect-0.3",
                     note="答案容器优先 + 题面特征收工 + 来源 citation chip 剔除 + 登录墙早退")
    n_c = len(recs) - n_a - n_b

    # run_index 重编号 + run_id 重生成
    seen = {}
    for r in sorted(recs, key=lambda x: (x["product"], x["task"], x.get("collected_at") or "")):
        k = (r["product"], r["task"])
        seen[k] = seen.get(k, 0) + 1
        r["run_index"] = seen[k]
        r["run_id"] = "%s-%s-%s-r%d" % (r["recorded_at"][:10], r["product"],
                                        r["task"].lstrip("T"), r["run_index"])

    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print("A(10-26)=%d  B(10-28中间轮)=%d  C(10-28定稿)=%d  合计 %d 条" % (n_a, n_b, n_c, len(recs)))
    cells = {}
    for r in recs:
        cells.setdefault(r["product"], {}).setdefault(r["task"], []).append(r["score"])
    for p in sorted(cells):
        line = []
        for t in ["T1", "T2", "T3", "T4", "T5"]:
            v = cells[p].get(t)
            line.append("%s:%s" % (t, ("%d次" % len(v)) if v else "未测"))
        print("  %-12s %s" % (p, "  ".join(line)))
    cols = sorted({r.get("collector") or "?" for r in recs})
    print("采集器版本:", " / ".join(cols))


if __name__ == "__main__":
    main()
