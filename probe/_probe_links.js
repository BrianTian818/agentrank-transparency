/**
 * VI-100 探测升级：首页链接发现 + 候选验证
 *
 * 背景（为什么必须做）：老探测器 _probe_all.js 只会猜固定路径
 *   base + '' / /pricing / /docs / /terms / /privacy / /refund / /security / /contact
 * 于是 AutoGen（Microsoft 官方文档站 + GitHub 仓库）只有 15/100 —— 它的文档在
 * /autogen/stable/、GitHub 链接在页面导航里，猜路径全 404，全部记 0。
 * 那测出来的是「爬虫运气」，不是「可核验度」。
 *
 * 本脚本的做法：加载首页 -> 抓渲染后页面上的全部 <a href> -> 按 URL + 锚文本分类
 * 成 pricing/docs/terms/privacy/refund/security/contact/github -> 逐个真机访问验证。
 *
 * 口径没有任何放松：本脚本只负责「记录真实抓取到的事实」，
 * 软 404 / 登录墙 / 被塞回应用壳 的判定统一留在 merge_probe.py 的 judge() 里——
 * 判定逻辑只有一份实现，避免「猜路径」和「发现链接」两套标准。
 */
const { chromium } = require('D:/2026-10-20-01-59-26/_tools/node_modules/playwright-core');
const fs = require('fs');
const path = require('path');
const R = require('./_probe_rules.js');
const C = require('./_probe_common.js');

const EXE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const ROOT = __dirname;
const MANIFEST = path.join(ROOT, 'geo/vi_products.json');
const OUT = process.env.LINK_OUT
  ? path.join(ROOT, process.env.LINK_OUT)
  : path.join(ROOT, 'geo/link_probe_2026-11.json');

const CONCURRENCY = parseInt(process.argv[2] || '5', 10);
// MIN_SCORE / 分类规则统一在 _probe_rules.js，首页发现与 sitemap 反查共用一份
const PRODUCT_TIMEOUT = 420000;      // 单个产品最多 7 分钟（要额外访问最多 12 个候选链接）
const MAX_ANCHORS = 400;
const MAX_VISIT = 12;                // 每个产品最多访问多少个候选链接

/* 多租户托管域名：这类主机上路径本身就是产品身份，resplit 时不能按 origin 截断 */
/** 在首页上抓锚点并按分类打分；同一域名内的链接才认，github 例外 */
async function collectLinks(page, base) {
  return await page.evaluate((MAX_ANCHORS) => {
    const out = [];
    const seen = new Set();
    const as = Array.from(document.querySelectorAll('a[href]')).slice(0, MAX_ANCHORS);
    for (const a of as) {
      const href = a.getAttribute('href') || '';
      if (!href || /^(javascript:|mailto:|tel:|#|data:)/i.test(href)) continue;
      let abs;
      try { abs = new URL(href, location.href).href; } catch (e) { continue; }
      const text = (a.innerText || a.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim().slice(0, 80);
      if (!text) continue;
      const key = abs;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ url: abs, text });
    }
    return { links: out, pageUrl: location.href };
  }, MAX_ANCHORS);
}

function scoreCandidates(baseRaw, homeFinal, links) {
  const baseHost = (() => { try { return new URL(baseRaw).host; } catch (e) { return ''; } })();
  const baseReg = R.regDomain(baseHost);
  const homePath = (() => { try { return new URL(homeFinal).pathname.replace(/\/+$/, ''); } catch (e) { return ''; } })();
  const buckets = {};
  let repo = '';

  for (const lk of links) {
    const r = R.classifyUrl(lk.url, lk.text, baseReg, homePath);
    if (r.repo && !repo) repo = r.repo;
    if (r.skip) continue;
    for (const [cat, s] of Object.entries(r.cats)) {
      (buckets[cat] = buckets[cat] || []).push({ url: lk.url, text: lk.text, score: s });
    }
  }

  // 同一 URL 可能被路径规则和子域名规则同时命中，按 URL 去重留最高分 —— 否则同一个
  // 页面会被访问两遍，白花一倍时间
  const picked = {};
  for (const [cat, arr] of Object.entries(buckets)) {
    const best = new Map();
    for (const x of arr) {
      const prev = best.get(x.url);
      if (!prev || x.score > prev.score) best.set(x.url, x);
    }
    // 最低分门槛：光靠锚文本命中（s=2）不够可信——实测 ada 的 "Accessibility" 被当成了条款页、
    // forethought 的一篇营销指南被当成文档中心。宁可记「未检出」也不能把错页算进分数。
    const list = Array.from(best.values())
      .filter(x => x.score >= R.MIN_SCORE)
      .sort((a, b) => b.score - a.score || a.url.length - b.url.length);
    picked[cat] = list.slice(0, 2).map(x => ({ url: x.url, text: x.text, score: x.score }));
  }
  return { picked, repo, anchors: links.length };
}

async function probeOne(ctx, t) {
  const rec = { id: t.id, base: R.originOf(t.site), productUrl: t.site, repo: '', candidates: {}, visits: {}, anchors: 0 };
  const page = await ctx.newPage();
  page.setDefaultTimeout(C.NAV_TIMEOUT);

  try {
    const homeRec = await C.visit(page, rec.base, { timeout: C.HOME_TIMEOUT, retries: 1 });
    rec.home = homeRec;
    if (homeRec.error || !homeRec.finalUrl) { await page.close(); rec._done = false; return rec; }
    const { links } = await collectLinks(page, rec.base);
    const { picked, repo, anchors } = scoreCandidates(rec.base, homeRec.finalUrl, links);
    rec.candidates = picked;
    rec.repo = repo;
    rec.anchors = anchors;

    // 逐个验证候选链接；每类最多访问 2 个，命中即停（省时间也不给厂商添压力）
    const flat = [];
    for (const cat of Object.keys(R.CATS)) {
      for (const c of (picked[cat] || [])) flat.push({ cat, ...c });
    }
    for (const c of flat.slice(0, MAX_VISIT)) {
      const key = c.cat + '|' + c.url;
      if (rec.visits[key]) continue;
      rec.visits[key] = await C.visit(page, c.url, { retries: 1 });
      rec.visits[key].cat = c.cat;
      rec.visits[key].anchorText = c.text;
      rec.visits[key].score = c.score;
    }
  } catch (e) {
    rec._error = String(e.message || e).slice(0, 160);
  }

  await page.close();
  rec._done = true;
  return rec;
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).items;
  let targets = manifest;
  const ONLY = (process.env.PROBE_ONLY || '').split(',').filter(Boolean);
  if (ONLY.length) targets = manifest.filter(t => ONLY.includes(t.id));

  const out = {};
  if (fs.existsSync(OUT)) { try { Object.assign(out, JSON.parse(fs.readFileSync(OUT, 'utf8'))); } catch (e) { } }

  const todo = targets.filter(t => !(out[t.id] && out[t.id]._done));
  console.log('manifest %d, targets %d, done %d, todo %d, concurrency %d',
    manifest.length, targets.length, targets.length - todo.length, todo.length, CONCURRENCY);

  const browser = await chromium.launch({ executablePath: EXE, headless: true });
  const ctxOpts = {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    locale: 'en-US',
    viewport: { width: 1280, height: 900 },
  };

  let idx = 0, done = 0;
  async function worker(wid) {
    const ctx = await browser.newContext(ctxOpts);
    while (idx < todo.length) {
      const t = todo[idx++];
      let rec;
      try { rec = await C.withTimeout(probeOne(ctx, t), PRODUCT_TIMEOUT, t.id); }
      catch (e) { rec = { id: t.id, base: R.originOf(t.site), _error: String(e.message || e).slice(0, 160), _done: false }; }
      out[t.id] = rec;
      done++;
      fs.writeFileSync(OUT, JSON.stringify(out, null, 2));
      const cats = Object.keys((rec.candidates || {}));
      console.log(`[w${wid} ${done}/${todo.length}] ${t.id.padEnd(22)} home=${(rec.home || {}).status || 0} links=${rec.anchors || 0} cats=${cats.join(',') || '-'} repo=${rec.repo || '-'}`);
    }
    await ctx.close();
  }

  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, Math.max(todo.length, 1)) }, (_, i) => worker(i + 1)));
  await browser.close();
  console.log('WROTE', OUT, Object.keys(out).length);
})();
