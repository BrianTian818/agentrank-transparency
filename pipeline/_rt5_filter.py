# -*- coding: utf-8 -*-
"""把「没拿到可判分输出」的运行挑出来，避免把它当成 0 分。

判定标准（宁可标未测，也不冤枉产品）：
  1. 空文件
  2. 只是登录墙 / 注册提示 / 额度拦截
  3. 去掉我们自己的提示词回显后，正文短得不成样子（< 120 字）
产物：_rt5_unusable.json（供出页使用），并把文件挪进 _rt5_runs/_unusable/
"""
import io, json, os, re, shutil
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _rt5_norm import strip_prompt, strip_cite, task_of
from _rt5_usable import judge

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "_rt5_runs")
BAD = os.path.join(RUNS, "_unusable")
os.makedirs(BAD, exist_ok=True)


def main():
    out = {}
    moved = 0
    for fn in sorted(os.listdir(RUNS)):
        if not fn.endswith(".txt") or fn.endswith(".raw.txt"):
            continue
        p = os.path.join(RUNS, fn)
        rawp = p[:-4] + ".raw.txt"
        src = rawp if (os.path.exists(rawp) and os.path.getsize(rawp)) else p
        if not os.path.getsize(p):
            # 跑失败的原因采集器已经写进 meta.why 了，直接沿用，
            # 别含糊地写成「未抓到任何输出」—— 那是我们的动作，不是产品发生的事。
            mp0 = p[:-4] + ".meta.json"
            why0 = ""
            if os.path.exists(mp0):
                try:
                    why0 = (json.load(io.open(mp0, encoding="utf-8")).get("why") or "")
                except Exception:
                    why0 = ""
            if why0.startswith("no_answer"):
                mw = re.search(r"等待\s*(\d+)s", why0)
                ml = re.search(r"答案净长度仅\s*(\d+)\s*字", why0)
                msg = "投递后未产出可判分正文（等待 %ss，答案净长度 %s 字）" % (
                    mw.group(1) if mw else "?", ml.group(1) if ml else "?")
            elif why0.startswith("wall:"):
                msg = "答案被锁在登录之后（" + why0[len("wall:"):].strip() + "）"
            elif why0:
                msg = "本次运行失败：" + why0[:60]
            else:
                msg = "本次运行未抓到任何输出"
            out.setdefault(fn.split("-")[0], {})[task_of(fn)] = msg
            # 必须把原因写回 meta —— 汇总清单 _rt5_unusable.json 是从 _unusable/ 的
            # meta.unusable_reason 重建的，不写的话下次运行这些原因就全丢了
            if os.path.exists(mp0):
                try:
                    mm = json.load(io.open(mp0, encoding="utf-8"))
                    mm["unusable_reason"] = msg
                    mm["status"] = "unusable"
                    json.dump(mm, io.open(mp0, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=2)
                except Exception:
                    pass
            shutil.move(p, os.path.join(BAD, fn))
            moved += 1
            continue
        raw = io.open(src, encoding="utf-8").read()
        meta = {}
        mp = p[:-4] + ".meta.json"
        if os.path.exists(mp):
            try:
                meta = json.load(io.open(mp, encoding="utf-8"))
            except Exception:
                meta = {}
        why = judge(raw, meta.get("prompt") or "")
        if why:
            prod = fn.split("-")[0]
            out.setdefault(prod, {})[task_of(fn)] = why
            meta["unusable_reason"] = why
            meta["status"] = "unusable"
            if os.path.exists(mp):
                json.dump(meta, io.open(mp, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=2)
            for f in (p, rawp, p[:-4] + ".png", p[:-4] + ".html", mp):
                if os.path.exists(f):
                    shutil.move(f, os.path.join(BAD, os.path.basename(f)))
            moved += 1

    # 关键：_unusable/ 是累积的。某格上一轮失败、这一轮跑成功了，旧产物还躺在里面，
    # agg 就会继续把它标成「未测」—— 我们明明已经测出来了，却还对外说没测，这是撒谎。
    # 所以：凡是本轮留在 _rt5_runs/ 里的有效格子，必须把 _unusable/ 里的同名旧产物清掉。
    ok_cells = set()
    for fn in sorted(os.listdir(RUNS)):
        if not fn.endswith(".txt") or fn.endswith(".raw.txt"):
            continue
        if not os.path.getsize(os.path.join(RUNS, fn)):
            continue
        t = task_of(fn)
        if t:
            ok_cells.add((fn.split("-")[0], t))
    cleared = 0
    for fn in sorted(os.listdir(BAD)):
        t = task_of(fn.replace(".meta.json", ".txt"))
        if not t:
            continue
        if (fn.split("-")[0], t) in ok_cells:
            try:
                os.remove(os.path.join(BAD, fn))
                cleared += 1
            except Exception:
                pass
    if cleared:
        print("清除 %d 个已补测成功的旧失败产物（避免残留「未测」标记）" % cleared)

    # 报告按 _unusable/ 目录累积统计，避免每次运行被覆盖
    agg = {}
    for fn in sorted(os.listdir(BAD)):
        if not fn.endswith(".meta.json"):
            continue
        try:
            m = json.load(io.open(os.path.join(BAD, fn), encoding="utf-8"))
        except Exception:
            continue
        prod = fn.split("-")[0]
        t = task_of(fn.replace(".meta.json", ".txt"))
        if t:
            agg.setdefault(prod, {})[t] = m.get("unusable_reason", "未产出可判分结果")
    io.open(os.path.join(HERE, "_rt5_unusable.json"), "w", encoding="utf-8").write(
        json.dumps(agg, ensure_ascii=False, indent=2))
    print("本轮剔除 %d 次运行（标为未测）" % moved)
    for prod, d in agg.items():
        for t, why in sorted(d.items()):
            print("  %-12s %s  %s" % (prod, t, why))


if __name__ == "__main__":
    main()
