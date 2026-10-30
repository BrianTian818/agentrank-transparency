/* sitemap 反查：给「首页找不到」的产品第二次机会。

为什么需要它：两步发现法已经能救回大部分产品，但仍有 59 个落在 D 档。它们的定价页 /
条款页 / 文档站常常根本不在首页导航里 —— 只在 sitemap 中。首页找不到不等于厂商没公开，
这时候去 sitemap 里反查才是「先去厂商自己说的地方找」的完整形态。

只跑还有缺失子项的产品；只验证缺失的分类。抓不到依然记「未检出」。
用法：node _probe_sitemap.js 4
*/
'use strict';
const { chromium } = require('D:/2026-10-20-01-59-26/_tools/node_modules/playwright-core');
const fs = require('fs');
const path = require('path');

const R = require('./_probe_rules.js');
const C = require('./_probe_common.js');

const EXE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const ROOT = __dirname;
const MANIFEST = path.join(ROOT, 'geo/vi_products.json');
const OUT = process.env.SITEMAP_OUT
  ? path.join(ROOT, process.env.SITEMAP_OUT)
  : path.join(ROOT, 'geo/sitemap_probe_2026-11.json');
const VI = process.env.VI_JSON || path.join(ROOT, 'geo/vi_2026-11.json');

const CONCURRENCY = parseInt(process.argv[2] || '4', 10);
const PRODUCT_TIMEOUT = 300000;      // 单个产品最多 5 分钟
const MAX_SITEMAP = 6;               // 最多递归几个子 sitemap
const MAX_LOCS = 20000;              // sitemap 太大只取前 N 条
const MAX_VISIT = 10;                // 每个产品最多真机验证几个候选
const PER_CAT = 2;

// 证据标签 -> 分类（与 geo/score_vi.py 的 add() 标签对应）
const NEED = [
  ['定价页公开存在', 'pricing'],
  ['公开文档 / 帮助中心', 'docs'],
  ['服务条款公开', 'terms'],
  ['隐私政策公开', 'privacy'],
  ['退款或安全页公开', 'refund'],
  ['可联系渠道', 'contact'],
];

function missingCats(it) {
  const bad = new Set();
  for (const e of it.evidence || []) {
    if (e.ok) continue;
    for (const [label, cat] of NEED) if (e.labelZh === label) bad.add(cat);
  }
  // 退款与安全页共用一个标签，任一命中就一起找
  if (bad.has('refund')) bad.add('security');
  return bad;
}

async function fetchSitemaps(page, base) {
  const found = [];
  const tried = new Set();

  async function grab(url, depth) {
    if (depth > 1 || found.length >= MAX_SITEMAP || tried.has(url)) return;
    tried.add(url);
    let body = '';
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: C.NAV_TIMEOUT });
        if (!resp || resp.status() >= 400) return;
        body = await page.evaluate(() => document.documentElement.textContent || '');
        break;
      } catch (e) {
        body = '';
      }
    }
    if (!body) return;
    const locs = [];
    const re = /<loc>\s*([^<]+?)\s*<\/loc>/gi;
    let m;
    while ((m = re.exec(body)) !== null && locs.length < MAX_LOCS) locs.push(m[1].trim());
    if (!locs.length) return;

    if (/<sitemapindex/i.test(body)) {
      for (const l of locs.slice(0, MAX_SITEMAP)) await grab(l, depth + 1);
      return;
    }
    found.push(...locs);
  }

  // robots.txt 里声明的 Sitemap 优先，没有再猜常规路径
  let robots = '';
  try {
    const r = await page.goto(base.replace(/\/+$/, '') + '/robots.txt',
      { waitUntil: 'domcontentloaded', timeout: C.NAV_TIMEOUT });
    if (r && r.status() < 400) {
      robots = await page.evaluate(() => document.documentElement.textContent || '');
    }
  } catch (e) { /* robots 拿不到就直接猜 */ }
  const declared = [];
  const rre = /^\s*sitemap:\s*(\S+)\s*$/gim;
  let rm;
  while ((rm = rre.exec(robots)) !== null) declared.push(rm[1]);

  for (const u of declared.slice(0, MAX_SITEMAP)) await grab(u, 0);
  if (!found.length) await grab(base.replace(/\/+$/, '') + '/sitemap.xml', 0);
  if (!found.length) await grab(base.replace(/\/+$/, '') + '/sitemap_index.xml', 0);
  return found;
}

async function probeOne(ctx, t, need) {
  const rec = { id: t.id, base: R.originOf(t.site), productUrl: t.site,
    candidates: {}, visits: {}, locs: 0, need: Array.from(need) };
  const page = await ctx.newPage();
  page.setDefaultTimeout(C.NAV_TIMEOUT);
  try {
    const locs = await fetchSitemaps(page, rec.base) || [];
    rec.locs = locs.length;
    if (!locs.length) { rec._done = true; rec._empty = 'no sitemap'; await page.close(); return rec; }

    const baseHost = (() => { try { return new URL(rec.base).host; } catch (e) { return ''; } })();
    const baseReg = R.regDomain(baseHost);
    const buckets = {};
    for (const loc of locs) {
      // sitemap 条目没有锚文本佐证，必须走「末段精确匹配」，否则术语表文章会被当成安全页、
      // 白皮书被当成文档中心、退款 Agent 产品页被当成退款政策页
      const cats = R.classifyStrict(loc, baseReg);
      for (const [cat, s] of Object.entries(cats)) {
        if (!need.has(cat)) continue;                  // 只补缺失的分类
        (buckets[cat] = buckets[cat] || []).push({ url: loc, text: '', score: s });
      }
    }
    const picked = {};
    for (const [cat, arr] of Object.entries(buckets)) {
      const best = new Map();
      for (const x of arr) {
        const prev = best.get(x.url);
        if (!prev || x.score > prev.score) best.set(x.url, x);
      }
      picked[cat] = Array.from(best.values())
        .sort((a, b) => b.score - a.score || a.url.length - b.url.length)
        .slice(0, PER_CAT);
    }
    rec.candidates = picked;

    const flat = [];
    for (const [cat, arr] of Object.entries(picked)) for (const c of arr) flat.push({ cat, ...c });
    for (const c of flat.slice(0, MAX_VISIT)) {
      const key = c.cat + '|' + c.url;
      rec.visits[key] = await C.visit(page, c.url, { retries: 1 });
      rec.visits[key].cat = c.cat;
      rec.visits[key].anchorText = '';
      rec.visits[key].score = c.score;
    }
    rec._done = true;
  } catch (e) {
    rec._error = String(e.message || e).slice(0, 160);
    rec._done = true;
  }
  await page.close();
  return rec;
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).items;
  const vi = JSON.parse(fs.readFileSync(VI, 'utf8'));
  const byId = {};
  for (const it of vi.items) byId[it.id] = it;

  let targets = [];
  const ONLY = (process.env.PROBE_ONLY || '').split(',').filter(Boolean);
  for (const t of manifest) {
    if (ONLY.length && !ONLY.includes(t.id)) continue;
    const it = byId[t.id];
    // 受阻产品（不在 items 里）也要跑：它们本来就没分，救回来是纯增益
    if (!it && !process.env.PROBE_FORCE) continue;
    const need = it ? missingCats(it) : new Set(Object.keys(R.CATS));
    if (!need.size) continue;
    targets.push([t, need]);
  }

  const out = {};
  if (fs.existsSync(OUT)) { try { Object.assign(out, JSON.parse(fs.readFileSync(OUT, 'utf8'))); } catch (e) { } }
  const todo = targets.filter(([t]) => !(out[t.id] && out[t.id]._done));
  console.log('manifest %d, 有缺失项 %d, done %d, todo %d, concurrency %d',
    manifest.length, targets.length, targets.length - todo.length, todo.length, CONCURRENCY);

  const browser = await chromium.launch({ executablePath: EXE, headless: true });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0 Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    locale: 'en-US', viewport: { width: 1280, height: 900 },
  });

  let idx = 0, done = 0;
  async function worker(wid) {
    for (;;) {
      const i = idx++;
      if (i >= todo.length) return;
      const [t, need] = todo[i];
      let rec;
      try {
        rec = await C.withTimeout(probeOne(ctx, t, need), PRODUCT_TIMEOUT, t.id);
      } catch (e) {
        rec = { id: t.id, base: R.originOf(t.site), _error: String(e.message || e).slice(0, 160), _done: true };
      }
      out[t.id] = rec;
      done++;
      const cats = Object.keys(rec.candidates || {});
      console.log(`[w${wid} ${done}/${todo.length}] ${t.id.padEnd(22)} locs=${rec.locs || 0} `
        + `cats=${cats.join(',') || '-'} visits=${Object.keys(rec.visits || {}).length}${rec._empty ? ' (' + rec._empty + ')' : ''}`);
      if (done % 10 === 0) fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
    }
  }
  await Promise.all(Array.from({ length: CONCURRENCY }, (_, i) => worker(i + 1)));
  fs.writeFileSync(OUT, JSON.stringify(out, null, 1));
  await browser.close();
  console.log('WROTE', OUT, Object.keys(out).length);
})();
