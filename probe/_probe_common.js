/* 探测器的共享访问层：首页链接发现（_probe_links.js）与 sitemap 反查（_probe_sitemap.js）
   用同一套「访问 + 记录事实」的实现，避免两边判定漂移。
   判定不在这里做 —— 判定统一在 geo/merge_probe.py 的 judge() 里。 */
'use strict';

const R = require('./_probe_rules.js');

const NAV_TIMEOUT = 25000;
const HOME_TIMEOUT = 40000;          // 首页给足时间，冷启动握手有时要 20s+
const SETTLE_TIMEOUT = 6000;

function withTimeout(p, ms, label) {
  let h;
  return Promise.race([
    p.finally(() => clearTimeout(h)),
    new Promise((_, rej) => { h = setTimeout(() => rej(new Error('watchdog timeout: ' + label)), ms); }),
  ]);
}

/** 访问一个 URL，记录「事实」不做判定（判定统一在 merge_probe.py 的 judge 里）
 *  注意 retries：实测每个 Edge 上下文的第一次导航常因 TLS/DNS 冷启动卡满 20s，
 *  第二次同样的 URL 只要 300ms。所以超时必须重试一次，否则会白白把
 *  明明能访问的官网判成「无法核验」。 */
async function visit(page, url, opts) {
  const timeout = (opts && opts.timeout) || NAV_TIMEOUT;
  const retries = (opts && opts.retries) || 0;
  const r = { url, status: 0, finalUrl: '', title: '', len: 0, head: '', prices: [], hasFree: false, hasMail: false };
  let resp = null, lastErr = '';
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
      lastErr = '';
      break;
    } catch (e) {
      lastErr = String(e.message || e).slice(0, 120);
      resp = null;
    }
  }
  try {
    if (!resp) throw new Error(lastErr || 'goto failed');
    r.status = resp ? resp.status() : 0;
    r.finalUrl = page.url();
    try { await page.waitForLoadState('networkidle', { timeout: SETTLE_TIMEOUT }); } catch (e) { /* SPA 可能永不 idle */ }
    await page.waitForTimeout(800);
    r.title = (await page.title()) || '';
    const readBody = () => page.evaluate(() => (document.body ? document.body.innerText : '') || '');
    let body = await readBody();
    // mkdocs / Sphinx 这类站点 hydration 慢，第一轮常常读到空 body —— 那会直接导致
    // 链接数为 0、整场探测白跑。再等 2s 重读一次。
    if (body.length < 100) { await page.waitForTimeout(2000); body = await readBody(); }
    r.len = body.length;
    r.head = body.slice(0, 300);
    const ctype = (resp && (resp.headers && resp.headers()['content-type'])) || '';
    if (!/text\/html/i.test(ctype)) r.notHtml = true;
    r.prices = R.priceCheck(body);
    r.hasFree = /\b(free plan|free tier|免费|free forever|get started free|start free|试用|free credits)\b/i.test(body);
    r.hasMail = /[\w.\-]+@[\w\-]+\.[\w.]{2,}/.test(body);
  } catch (e) {
    r.error = String(e.message || e).slice(0, 120);
  }
  return r;
}

module.exports = {
  NAV_TIMEOUT, HOME_TIMEOUT, SETTLE_TIMEOUT, withTimeout, visit,
};
