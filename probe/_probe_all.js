/**
 * VI-100 扩量：通用浏览器探测
 *
 * 与 _reprobe_all.js 的区别（那个是 12 个目标的硬编码版）：
 * 1. 目标从 geo/vi_products.json 读（167 个），不再硬编码
 * 2. 支持并发（默认 4 个上下文）+ 断点续跑
 * 3. 记录真实 TTFB（navigation.responseStart - requestStart），
 *    而不是把「到 DOM 解析完」当成首字节时间——那会系统性压低可用性分
 * 4. 探测基准用 origin（https://host），因为名单里有 zapier.com/agents 这类深链，
 *    /pricing 要挂在域名根上才有意义
 *
 * 为什么必须用真浏览器：Cloudflare 会挡掉 urllib（chatgpt.com / openai.com 在服务器上恒 403），
 * 而真浏览器指纹能过；SPA 也只有真浏览器能渲染出真实标题与正文，
 * 从而识别出「/pricing 其实被塞回了首页」这种假 200。
 */
const { chromium } = require('D:/2026-10-20-01-59-26/_tools/node_modules/playwright-core');
const fs = require('fs');
const path = require('path');

const EXE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const ROOT = __dirname;
const MANIFEST = path.join(ROOT, 'geo/vi_products.json');
const OUT = process.env.PROBE_OUT
  ? path.join(ROOT, process.env.PROBE_OUT)
  : path.join(ROOT, 'geo/browser_probe_2026-11.json');

const PATHS = ['', '/pricing', '/docs', '/terms', '/privacy', '/refund', '/security', '/contact'];
const CONCURRENCY = parseInt(process.argv[2] || '5', 10);
const NAV_TIMEOUT = 25000;
const PRODUCT_TIMEOUT = 180000;   // 单个产品最多 3 分钟

function priceCheck(text) {
  const priceRe = /([\$€¥£]\s?\d[\d,\.]*)|(\d[\d,\.]*\s?(USD|CNY|EUR|GBP))/g;
  const periodRe = /(per month|\/mo\b|\/month|每月|per seat|annually|\/year|\/yr|monthly)/i;
  let m, hits = [];
  while ((m = priceRe.exec(text)) !== null) {
    const i = m.index;
    const win = text.slice(Math.max(0, i - 150), i + 150);
    if (periodRe.test(win)) hits.push(m[0].trim());
    if (hits.length > 6) break;
  }
  return hits;
}

/* 多租户托管域名：github.io / vercel.app 这类主机上，路径本身就是产品身份
   （microsoft.github.io/autogen/ 才是产品，microsoft.github.io 只是组织的 Pages 首页）。
   对这些主机若按 origin 截断，探到的根本不是该产品的公开信息，分数也就失了意义。
   github.com 的仓库地址同理——要探的是仓库页，不是 github.com 首页。 */
const MULTI_TENANT = /(github\.io|gitlab\.io|vercel\.app|netlify\.app|pages\.dev|web\.app|surge\.sh|codeberg\.page|herokuapp\.com)$/i;

function originOf(u) {
  try {
    const url = new URL(u);
    if (MULTI_TENANT.test(url.host) || /(^|\.)github\.com$/i.test(url.host)) {
      return u.replace(/\/+$/, '');
    }
    return url.origin;
  } catch (e) { return u.replace(/\/.*$/, ''); }
}

/** 单个产品的看门狗：卡住的产品直接放弃并记原因，绝不能拖死整个 worker。
 *  之前就是因为某个产品的 evaluate/networkidle 永久挂起，
 *  4 个 worker 全部僵住，跑了 3 小时停在 85/167 没人发现。 */
function withTimeout(p, ms, label) {
  let h;
  return Promise.race([
    p.finally(() => clearTimeout(h)),
    new Promise((_, rej) => { h = setTimeout(() => rej(new Error('watchdog timeout: ' + label)), ms); })
  ]);
}

async function probeOne(ctx, t) {
  const rec = { id: t.id, base: originOf(t.site), productUrl: t.site, pages: {} };
  const page = await ctx.newPage();
  page.setDefaultTimeout(NAV_TIMEOUT);
  for (const p of PATHS) {
    const url = rec.base + p;
    const r = { url, status: 0, ok: false, finalUrl: '', title: '', ms: -1, len: 0 };
    const t0 = Date.now();
    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
      r.status = resp ? resp.status() : 0;
      r.finalUrl = page.url();
      try { await page.waitForLoadState('networkidle', { timeout: 6000 }); } catch (e) { /* 单页应用可能永远不 idle */ }
      await page.waitForTimeout(1200);
      r.title = (await page.title()) || '';
      const body = await page.evaluate(() => (document.body ? document.body.innerText : '') || '');
      r.len = body.length;
      r.head = body.slice(0, 300);
      // 真实 TTFB：首字节 - 请求发出。拿不到时才退回整段导航耗时，并在字段里标明
      const ttfb = await page.evaluate(() => {
        const n = performance.getEntriesByType('navigation')[0];
        if (n && n.responseStart > 0 && n.requestStart > 0) return Math.round(n.responseStart - n.requestStart);
        const p2 = performance.getEntriesByType('paint')[0];
        return p2 ? -1 : -1;
      }).catch(() => -1);
      r.ms = ttfb > 0 ? ttfb : (Date.now() - t0);
      r.msIsTtfb = ttfb > 0;
      r.ok = r.status >= 200 && r.status < 400 && r.len > 100;
      if (r.ok) {
        r.prices = priceCheck(body);
        r.hasFree = /\b(free plan|free tier|免费|free forever|get started free|start free|试用|free credits)\b/i.test(body);
        r.hasMail = /[\w.\-]+@[\w\-]+\.[\w.]{2,}/.test(body);
      }
    } catch (e) {
      r.error = String(e.message || e).slice(0, 120);
      r.ms = Date.now() - t0;
    }
    rec.pages[p || '/'] = r;
  }
  await page.close();
  rec._done = true;
  return rec;
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).items;
  const out = {};
  if (fs.existsSync(OUT)) { try { Object.assign(out, JSON.parse(fs.readFileSync(OUT, 'utf8'))); } catch (e) { } }

  const LIMIT = parseInt(process.env.PROBE_LIMIT || '0', 10);
  const ONLY = (process.env.PROBE_ONLY || '').split(',').filter(Boolean);
  let pool = ONLY.length ? manifest.filter(t => ONLY.includes(t.id)) : manifest;
  // 重跑受阻产品时要把已存在的旧记录剔掉，否则会被断点逻辑当成「已完成」跳过
  if (process.env.PROBE_FORCE === '1') {
    for (const id of ONLY) delete out[id];
  }
  const todo = pool.filter((t) => !(out[t.id] && out[t.id]._done)).slice(0, LIMIT || undefined);
  console.log('manifest %d, done %d, todo %d, concurrency %d', manifest.length, manifest.length - todo.length, todo.length, CONCURRENCY);

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
      try { rec = await withTimeout(probeOne(ctx, t), PRODUCT_TIMEOUT, t.id); }
      catch (e) { rec = { id: t.id, base: originOf(t.site), pages: {}, _error: String(e.message || e).slice(0, 160) }; }
      out[t.id] = rec;
      done++;
      fs.writeFileSync(OUT, JSON.stringify(out, null, 2));
      const h = (rec.pages && rec.pages['/']) || {};
      console.log(`[w${wid} ${done}/${todo.length}] ${t.id.padEnd(24)} home=${h.status || 0} ttfb=${h.ms}ms ${h.title ? String(h.title).slice(0, 30) : (rec._error || '')}`);
    }
    await ctx.close();
  }

  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, todo.length) }, (_, i) => worker(i + 1)));
  await browser.close();
  console.log('WROTE', OUT, Object.keys(out).length);
})();
