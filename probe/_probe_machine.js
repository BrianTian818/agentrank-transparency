/**
 * VI-100 扩量：机器可读性 + 开源仓库线索探测（轻量，不渲染页面）
 *
 * 为什么要单独跑这一趟：
 * robots.txt / sitemap.xml / llms.txt 这些静态文件以前靠 urllib 抓，
 * 但 urllib 会被 Cloudflare 挡（chatgpt.com 在服务器上恒 403），
 * 于是「抓不到」被记成「没有 sitemap、没有结构化数据」，直接扣掉开放度分。
 * 这跟把超时当低分是同一类错误。这里改用浏览器自己的网络栈（同一 UA、同一出口），
 * 站点对浏览器放行，我们就能拿到真实答案。
 *
 * 主页 HTML 用同一个请求上下文取，用来判 JSON-LD 与 GitHub 仓库链接
 * （仓库链接后面交给 GitHub API 补星标 / 许可证，与老管线一致）。
 */
const { chromium } = require('D:/2026-10-20-01-59-26/_tools/node_modules/playwright-core');
const fs = require('fs');
const path = require('path');

const EXE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const ROOT = __dirname;
const MANIFEST = path.join(ROOT, 'geo/vi_products.json');
const OUT = path.join(ROOT, 'geo/machine_probe_2026-11.json');
const CONCURRENCY = parseInt(process.argv[2] || '4', 10);

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

const GH_RE = /github\.com\/([A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+)/;

async function probeOne(ctx, t) {
  const base = originOf(t.site);
  const rec = { id: t.id, base, machineReadable: {}, repo: '' };
  const req = ctx.request;

  for (const [key, file] of [['robots_txt', 'robots.txt'], ['sitemap_xml', 'sitemap.xml'], ['llms_txt', 'llms.txt']]) {
    try {
      const r = await req.get(base + '/' + file, { timeout: 15000 });
      rec.machineReadable[key] = r.status() === 200;
    } catch (e) { rec.machineReadable[key] = false; }
  }

  try {
    const r = await req.get(base + '/', { timeout: 20000 });
    const html = r.status() >= 200 && r.status() < 400 ? await r.text() : '';
    rec.machineReadable.jsonld = /application\/ld\+json/i.test(html);
    const m = GH_RE.exec(html || '');
    if (m) rec.repo = m[1].replace(/\.(git|md|png|jpg|svg)$/i, '').replace(/\.+$/, '');
  } catch (e) {
    rec.machineReadable.jsonld = false;
    rec.error = String(e.message || e).slice(0, 120);
  }
  rec._done = true;
  return rec;
}

(async () => {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8')).items;
  const out = {};
  if (fs.existsSync(OUT)) { try { Object.assign(out, JSON.parse(fs.readFileSync(OUT, 'utf8'))); } catch (e) { } }
  const todo = manifest.filter((t) => !(out[t.id] && out[t.id]._done));
  console.log('todo %d / %d, concurrency %d', todo.length, manifest.length, CONCURRENCY);

  const browser = await chromium.launch({ executablePath: EXE, headless: true });
  const ctxOpts = {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    locale: 'en-US',
  };

  let idx = 0, done = 0;
  async function worker(wid) {
    const ctx = await browser.newContext(ctxOpts);
    while (idx < todo.length) {
      const t = todo[idx++];
      let rec;
      try { rec = await probeOne(ctx, t); }
      catch (e) { rec = { id: t.id, base: originOf(t.site), machineReadable: {}, repo: '', _error: String(e.message || e).slice(0, 160) }; }
      out[t.id] = rec; done++;
      fs.writeFileSync(OUT, JSON.stringify(out, null, 2));
      if (done % 10 === 0) console.log(`[w${wid}] ${done}/${todo.length}`);
    }
    await ctx.close();
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, todo.length) }, (_, i) => worker(i + 1)));
  await browser.close();
  console.log('WROTE', OUT, Object.keys(out).length);
})();
