/* RT-5 真实跑测器 —— 本机 Edge + 持久化 profile（真实浏览器指纹，可过风控）
 * 用法: node _rt5_local.js <product> <T1..T5> <run> [--head]
 * 产出: _rt5_runs/<product>-T<n>-r<r>.txt / .meta.json / .png
 *
 * 铁律：只记录真实发生的事。跑不出来就记 status=fail + 原因，绝不留空也不编造。
 */
const { chromium } = require('D:/2026-10-20-01-59-26/_tools/node_modules/playwright-core');
const fs = require('fs');
const path = require('path');

const EXE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const DIR = __dirname;
const OUT = path.join(DIR, '_rt5_runs');
fs.mkdirSync(OUT, { recursive: true });

// ---- 五道题的提示词：口径与 realtest_grade.py 的判分器严格一致 ----
// 单一事实来源：rt5_prompts.json（自测与社区投稿共用同一份原文，结构上杜绝漂移）
const TASKS = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'rt5_prompts.json'), 'utf8')).prompts;

const INPUT_SEL = 'textarea, [contenteditable="true"]';

const PRODUCTS = {
  perplexity: { url: 'https://www.perplexity.ai/', input: INPUT_SEL },
  kimi:       { url: 'https://www.kimi.com/', input: INPUT_SEL },
  skywork:    { url: 'https://skywork.ai/', input: INPUT_SEL },
  minimax:    { url: 'https://agent.minimax.io/', input: INPUT_SEL },
  manus:      { url: 'https://manus.im/', input: INPUT_SEL },
  genspark:   { url: 'https://www.genspark.ai/', input: INPUT_SEL },
  flowith:    { url: 'https://flowith.io/', input: INPUT_SEL },
  metaso:     { url: 'https://metaso.cn/', input: INPUT_SEL },
  huggingchat:{ url: 'https://huggingface.co/chat/', input: INPUT_SEL },
  blackbox:   { url: 'https://www.blackbox.ai/', input: INPUT_SEL },
  // 覆盖度探测里唯一「匿名可跑通」的国产产品（2026-10-28 实测）
  chatglm:    { url: 'https://chatglm.cn/', input: INPUT_SEL },
};

const sleep = ms => new Promise(r => setTimeout(r, ms));

const product = process.argv[2];
const task = (process.argv[3] || '').toUpperCase();
const runNo = process.argv[4] || '1';
const HEADLESS = process.argv.indexOf('--head') < 0;   // 默认有头（真实指纹）

const P = PRODUCTS[product];
const prompt = TASKS[task];
if (!P || !prompt) { console.error('BAD_ARGS product=%s task=%s', product, task); process.exit(2); }

const base = path.join(DIR, '_rt5_runs', '%s-T%s-r%s'.replace(/%s/g, () => '').trim() || '');
const stamp = '%s-T%s-r%s';
const outT = path.join(OUT, `${product}-${task}-r${runNo}.txt`);
const outM = path.join(OUT, `${product}-${task}-r${runNo}.meta.json`);
const outP = path.join(OUT, `${product}-${task}-r${runNo}.png`);
// --fresh：每次用一次性临时 profile（有些产品只对「全新匿名会话」给额度，如秘塔）
const FRESH = process.argv.indexOf('--fresh') >= 0;
let tmpRoot = null;
const profDir = FRESH
  ? (tmpRoot = fs.mkdtempSync(path.join(require('os').tmpdir(), 'ar-rt5-')))
  : path.join(DIR, '_rt5_profile_' + product);
const FRESH_INIT = { locale: 'zh-CN', timezoneId: 'Asia/Shanghai' };

// 抽正文：优先 CodeMirror（保留缩进）→ pre → 回答容器 → body
async function grab(page) {
  return page.evaluate(() => {
    const vis = e => e && (e.offsetParent !== null || e.getClientRects().length > 0);
    // textContent 会把 <script>/<style> 也捞进来（秘塔整页就是一堆内联脚本），必须剔掉
    const clean = (el) => {
      if (!el) return '';
      const cl = el.cloneNode(true);
      // citation chip 两条路径都要剔：innerText 靠临时 display:none（见 both），
      // textContent 这条靠克隆后直接删节点 —— 否则 clean 分支照样把来源域名带进 CSV
      cl.querySelectorAll('script,style,noscript,template,svg,iframe,'
        + '[class*="citation" i],[data-pplx-citation]').forEach(n => n.remove());
      return (cl.textContent || '').replace(/[ \t]+\n/g, '\n');
    };
    // innerText 会被 content-visibility 裁掉，clean(textContent) 不会：取更长的那个
    const both = (el, tag) => {
      // 来源引用 chip（Perplexity 的 <span class="citation" data-pplx-citation>）是 UI，
      // 不是模型输出。混进 CSV 行尾 / JSON 对象中间会把结构化输出直接弄成解析失败，
      // 然后被我们判 0 分 —— 那是冤枉产品。innerText 拿不到「隐藏后」的结果，
      // 所以先临时 display:none 再读，读完立刻还原（布局不受影响）。
      const chips = [...el.querySelectorAll('[class*="citation" i], [data-pplx-citation]')];
      const prev = chips.map(e => e.style.display);
      chips.forEach(e => { e.style.display = 'none'; });
      const a = el.innerText || '';
      chips.forEach((e, i) => { e.style.display = prev[i]; });
      const b = clean(el);
      return a.length >= b.length ? { how: tag + '(inner)', text: a }
                                  : { how: tag + '(clean)', text: b };
    };
    const cm = [...document.querySelectorAll('.cm-line')].filter(vis);
    if (cm.length) return { how: 'cm-line', text: cm.map(e => e.innerText.replace(/ /g, ' ')).join('\n') };
    // 只认「答案容器」。'[class*="content" i]' 会命中整个 app 外壳（侧栏 + 输入框 + 提示词回显），
    // 长度肯定过 120 于是直接 return —— 抓到的却是 UI 骨架，不是答案。必须排到最后。
    for (const s of ['[data-testid="answer"]', '.prose', 'article', '[class*="answer" i]',
                     // 智谱清言把正文放在 .markdown-body 里，没有这行就只能抓整个 main（含侧栏 + 提示词回显）
                     '[class*="markdown" i]', '[class*="result" i]']) {
      const el = document.querySelector(s);
      if (el) {
        const r = both(el, s);
        if (r.text.length > 120) return r;
      }
    }
    const pre = [...document.querySelectorAll('pre')].filter(vis);
    if (pre.length) {
      const t = pre.map(e => (e.textContent || '')).join('\n\n');
      if (t.trim()) return { how: 'pre(text)', text: t };
    }
    for (const s of ['main', '[class*="content" i]', 'body']) {
      const el = document.querySelector(s);
      if (el) {
        const r = both(el, s);
        if (r.text) return { how: r.how, text: r.text.slice(0, 30000) };
      }
    }
    return { how: 'none', text: '' };
  });
}

// 与 _rt5_usable.py 的 after_prompt 同口径：截掉最后一次提示词回显，只留正文。
// 不去回显的话，侧栏 + 提示词本身就够 400 字，稳定判定会误以为「答案已经出来了」。
function stripEcho(text, prompt) {
  if (!prompt) return text;
  const sq = s => s.replace(/\s+/g, '');
  const tail = sq(prompt).slice(-40);
  if (!tail) return text;
  const idx = sq(text).lastIndexOf(tail);
  if (idx < 0) return text;
  const target = idx + tail.length;
  let cnt = 0;
  for (let j = 0; j < text.length; j++) {
    if (!/\s/.test(text[j])) {
      cnt++;
      if (cnt === target) return text.slice(j + 1);
    }
  }
  return text;
}

// 答案净长度（去空白、去引用角标）—— 稳定判定只看这个数
function ansLen(text, prompt) {
  return stripEcho(text, prompt).replace(/\[\[\d+\]\]/g, '').replace(/\s+/g, '').length;
}

// 「答案真的出来了」的题面特征。
// 光靠长度门槛会在两个方向上都错：① UI 骨架凑够长度 → 误判成"跑通了"；
// ② T3 的 CSV 天生只有 140 字 → 误判成"没跑出来"。所以按题目认特征，不认长度。
const SIG = {
  T1: /def\s+normalize_phone/,
  T2: /https?:\/\//,
  T3: /city\s*,\s*province/i,
  T4: /(第\s*\d+\s*页|\n\s*1[.、]|##\s+\S)/,
  T5: /("days"|days\s*[:：]|spots)/i,
};

// 登录 / 注册墙。出现这些就说明「测不了」，不是「跑得差」—— 立刻收工并如实记录，
// 没必要再干等 4 分钟。与 _rt5_usable.py 的 WALL 同口径，另补扫码类关键词。
const WALLS = ['微信扫码', '扫码登录', '请登录', '登录后继续', '登录后', '登录以继续',
               '创建账号', '未登录状态', '请先登录', 'Sign in to continue',
               'Log in to continue', '注册并重复', '您的消息已保存'];

(async () => {
  const ctx = await chromium.launchPersistentContext(profDir, {
    executablePath: EXE,
    headless: HEADLESS,
    viewport: { width: 1360, height: 940 },
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled'],
    locale: FRESH_INIT.locale,
    timezoneId: FRESH_INIT.timezoneId,
  });
  await ctx.grantPermissions(['clipboard-read', 'clipboard-write']);
  const page = (await ctx.pages())[0] || await ctx.newPage();
  page.setDefaultTimeout(30000);

  const log = [];
  const meta = { product, task, run: Number(runNo), url: P.url, prompt,
    startedAt: new Date().toISOString(),
    // 采集器版本：改抓取/判定逻辑必须升版本，和判分器一个待遇 —— 版本间成绩不可比
    collector: 'rt5-collect-0.3',
    collector_note: '答案容器优先 + 题面特征收工 + 来源 citation chip 剔除 + 登录墙早退',
    // ⚠ 0.3 的已知缺陷（2026-10-28 发现，未修，未升版本）：
    //   智谱清言的正文里混进了它的思考过程（"ChatGLM语音梦幻杰思考结束…我需要搜索最新数据…
    //   10 个来源…"），说明答案区选择器命中了包着推理链的外层容器。
    //   后果：T3 no_header / T4 no_h2 / T5 json_error 全判 0 —— 这是采集器的锅，不是产品的锅。
    //   修复方向：0.4 需要在抓到正文后剥离思考过程容器（按产品白名单 + 首个代码块/表格/JSON
    //   起始位置双重定位），并在重跑验证通过后才允许升版本。没验证过就等于没修。
    grader: 'rt5-judge-0.2', log };

  try {
    await page.goto(P.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await sleep(6000);
    log.push('loaded ' + page.url());

    // 关掉可能的登录弹窗
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(1200);
    for (const sel of ['[data-testid="login-modal"] [aria-label="Close"]',
                       '[data-testid="login-modal"] button:has-text("×")',
                       '[aria-label="Close"]', 'button[aria-label="关闭"]']) {
      const el = page.locator(sel).first();
      if (await el.count()) { await el.click({ timeout: 2500 }).catch(() => {}); log.push('closed modal ' + sel); break; }
    }

    // 定位输入框并输入
    let typed = false;
    for (const sel of P.input.split(',')) {
      const n = await page.locator(sel.trim()).count();
      if (!n) continue;
      for (let i = 0; i < Math.min(n, 6); i++) {
        const el = page.locator(sel.trim()).nth(i);
        try {
          if (!(await el.isVisible({ timeout: 1500 }))) continue;
          await el.click({ timeout: 4000 });
          await page.keyboard.type(prompt, { delay: 8 });
          typed = true; log.push('typed into ' + sel.trim() + '#' + i);
          break;
        } catch (e) { /* 换下一个 */ }
      }
      if (typed) break;
    }
    if (!typed) throw new Error('no_input_visible');
    await page.keyboard.press('Enter');
    log.push('submitted at ' + new Date().toISOString());

    // 等答案稳定：连续 3 次长度一致
    // 收工条件 = 「题面特征出现了」且「答案净长度连续两次不变」。
    // 只用长度会在两个方向上都判错：UI 骨架凑够长度被当成跑通；T3 的 CSV 天生只有 140 字被当成没跑出来。
    const sig = SIG[task] || /[\s\S]/;
    const ANS_FLOOR = 60;          // 兜底：低于这个数几乎肯定是骨架，不是答案
    let last = -1, same = 0, waited = 0, best = null, bestL = -1, sigHit = false;
    while (waited < 240000) {
      await sleep(5000); waited += 5000;
      // 滚一遍，逼出被 content-visibility 折叠的内容
      await page.evaluate(() => { try { window.scrollTo(0, document.body.scrollHeight); } catch (e) {} }).catch(() => {});
      const g = await grab(page);
      // 稳定判定只看「答案净长度」：整页长度会被侧栏撑住，答案还没出来就已经「稳定」了
      const L = ansLen(g.text, prompt);
      if (L > bestL) { bestL = L; best = g; }
      // 特征必须验在「剥掉提示词回显之后的正文」上。
      // 秘塔用持久化 profile，页面上还挂着上一题的对话 —— 整段文本里当然能找到
      // 「第 1 页」这种字样，那是上一轮的答案，不是这一轮的。
      if (sig.test(stripEcho(g.text, prompt))) sigHit = true;
      if (L === last && L >= ANS_FLOOR && sigHit) { same++; } else { same = 0; }
      last = L;
      // 「正在加载/思考中」也算长度稳定，必须排除，否则会抓到半成品
      const busy = /正在加载|加载中|思考中|思考了|生成中|正在生成|Thinking|Generating|Streaming/i
        .test(g.text.slice(-500));
      log.push(`poll ${waited / 1000}s ans=${L} same=${same} busy=${busy} sig=${sigHit} how=${g.how}`);
      if (same >= 2 && !busy) break;
      // 题面特征一直没出现时，先看看是不是被登录墙挡了 —— 是就别再等 4 分钟
      if (!sigHit && (waited % 15000 === 0)) {
        const ptxt = await page.evaluate(() => document.body.innerText || '').catch(() => '');
        const hit = WALLS.filter(w => ptxt.includes(w));
        if (hit.length) {
          throw new Error('wall: ' + hit.slice(0, 3).join('/'));
        }
      }
    }
    if (!sigHit || bestL < ANS_FLOOR) {
      // 答案是真没出来（登录墙 / 限流 / 卡住），如实记原因，绝不拿骨架当 0 分交差
      const ptxt = await page.evaluate(() => document.body.innerText || '').catch(() => '');
      const hit = WALLS.filter(w => ptxt.includes(w));
      if (hit.length) throw new Error('wall: ' + hit.slice(0, 3).join('/'));
      throw new Error('no_answer: 等待 ' + (waited / 1000) + 's 后'
        + (sigHit ? '' : '未出现题面特征')
        + '，答案净长度仅 ' + bestL + ' 字（兜底门槛 ' + ANS_FLOOR + '），判定为未产出');
    }
    // 稳定后再多等 6 秒收尾
    await sleep(6000);
    const g2 = await grab(page);
    if (ansLen(g2.text, prompt) > bestL) best = g2;

    // 优先用「复制」按钮拿高保真文本
    // 优先用「复制」按钮拿产品自己认定的权威 markdown
    let clip = '';
    for (const sel of [
      'button[aria-label*="opy" i]', 'button[title*="opy" i]',
      'button[aria-label*="复制"]', 'button[title*="复制"]',
      '[data-testid*="copy"]',
    ]) {
      try {
        const btn = page.locator(sel).last();
        if (!(await btn.count())) continue;
        await btn.click({ timeout: 6000 });
        await sleep(1200);
        clip = await page.evaluate(() => navigator.clipboard.readText());
        if (clip && clip.length > 80) { log.push('clipboard via ' + sel); break; }
        clip = '';
      } catch (e) { clip = ''; }
    }

    // 剪贴板 vs 抓取：**优先剪贴板**。
    // 教训（2026-10-28）：Perplexity 的 UI 会把引用来源 chip（finance.ifeng / trip）混进
    // innerText，正好插在 CSV 行尾和 JSON 对象之间 —— 整段结构化输出直接解析失败，
    // 被判 0 分。这不是产品的锅，是我们抽取的锅。产品自己的「复制」导出才是权威原文。
    // 防御：剪贴板内容也要过题面特征；不过关才退回页面抓取。
    let pick;
    if (clip && clip.length > 80 && sig.test(stripEcho(clip, prompt))) {
      pick = { how: 'clipboard', text: clip };
    } else if (best && sig.test(stripEcho(best.text, prompt))) {
      pick = best;
    } else if (clip && clip.length > 80) {
      pick = { how: 'clipboard', text: clip };
    } else {
      pick = best || { how: 'none', text: '' };
    }
    const rawText = pick.text;

    // 回答区的原始 HTML 也留一份：Markdown 标记（## / -）会被渲染掉，
    // 必须从结构化 HTML 还原，否则「格式遵循」这项永远判 0 —— 那是我们的锅。
    // 注意：必须从「剔掉 citation chip 之后的克隆」里取 —— 否则 _rt5_norm.py 走
    // html2md 时会把来源域名原样带进正文，CSV/JSON 直接解析失败。
    const ansHtml = await page.evaluate(() => {
      for (const s of ['[data-testid="answer"]', '.prose', 'article',
                       '[class*="answer" i]', '[class*="markdown" i]',
                       '[class*="result" i]', 'main']) {
        const el = document.querySelector(s);
        if (el && (el.innerHTML || '').length > 80) {
          const cl = el.cloneNode(true);
          cl.querySelectorAll('[class*="citation" i], [data-pplx-citation]').forEach(n => n.remove());
          return cl.innerHTML;
        }
      }
      return '';
    }).catch(() => '');
    if (ansHtml) fs.writeFileSync(outT.replace(/\.txt$/, '.html'), ansHtml, 'utf8');

    // 逐字原文先落盘当证据；归一化统一交给 _rt5_norm.py（单一实现，避免两处逻辑漂移）
    fs.writeFileSync(outT.replace(/\.txt$/, '.raw.txt'), rawText, 'utf8');
    fs.writeFileSync(outT, rawText, 'utf8');
    meta.raw_sha1 = require('crypto').createHash('sha1').update(rawText, 'utf8').digest('hex').slice(0, 12);
    try { await page.screenshot({ path: outP, fullPage: false }); } catch (e) {}

    Object.assign(meta, {
      status: 'ok', how: pick.how,
      len: rawText.length, waitedMs: waited + 6000,
      finishedAt: new Date().toISOString(),
    });
    fs.writeFileSync(outM, JSON.stringify(meta, null, 2), 'utf8');
    console.log(JSON.stringify({ ok: true, product, task, run: runNo, len: rawText.length,
      how: meta.how, waited: meta.waitedMs,
      head: rawText.slice(0, 200).replace(/\s+/g, ' ') }));
  } catch (e) {
    const why = String(e.message).split('\n')[0].slice(0, 200);
    log.push('ERR ' + why);
    Object.assign(meta, { status: 'fail', why, finishedAt: new Date().toISOString() });
    fs.writeFileSync(outM, JSON.stringify(meta, null, 2), 'utf8');
    fs.writeFileSync(outT, '', 'utf8');
    try { await page.screenshot({ path: outP, fullPage: false }); } catch (e2) {}
    console.log(JSON.stringify({ ok: false, product, task, run: runNo, why }));
  }
  await ctx.close();
  if (tmpRoot) { try { fs.rmSync(tmpRoot, { recursive: true, force: true }); } catch (e) {} }
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
