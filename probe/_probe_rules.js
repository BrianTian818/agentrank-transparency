/* 链接分类规则的唯一实现。
   首页链接发现（_probe_links.js）与 sitemap 反查（_probe_sitemap.js）必须共用这一份 ——
   判定标准若有两份，同一个页面会因「先撞见哪个」得到不同结论。 */
'use strict';

const MIN_SCORE = 3;   // 候选入选门槛：URL 命中(3) 或 子域名命中(4)，单靠锚文本(2) 不算

const MULTI_TENANT = /(github\.io|gitlab\.io|vercel\.app|netlify\.app|pages\.dev|web\.app|surge\.sh|codeberg\.page|herokuapp\.com)$/i;
const MULTI_SUFFIX = ['com.cn', 'co.uk', 'com.au', 'co.jp', 'com.br', 'co.kr', 'com.hk', 'org.cn', 'net.cn'];

function originOf(u) {
  try {
    const url = new URL(u);
    // 多租户托管域名上，路径本身就是产品身份：microsoft.github.io/autogen 截成
    // microsoft.github.io 会探到组织的 Pages 首页，分数失去意义
    if (MULTI_TENANT.test(url.host) || /(^|\.)github\.com$/i.test(url.host)) {
      return u.replace(/\/+$/, '');
    }
    return url.origin;
  } catch (e) {
    return u.replace(/\/.*$/, '');
  }
}

function regDomain(host) {
  const h = String(host || '').toLowerCase().replace(/:\d+$/, '');
  const parts = h.split('.');
  if (parts.length <= 2) return h;
  const last2 = parts.slice(-2).join('.');
  if (MULTI_SUFFIX.includes(last2)) return parts.slice(-3).join('.');
  return last2;
}

/* 每个分类两套正则：url 看路径、text 看锚文本。英文锚文本一律加词边界，
   否则 "planners" 会被当成 pricing（taskade 的 /apps/planners 就这么中过招）。 */
const CATS = {
  pricing: {
    url: /(^|\/)(pricing|plans|price|prices|subscribe|subscription|buy|purchase|upgrade|billing|plans-and-pricing)(\/|$|-|_|\.)/i,
    textEn: /\b(pricing|plans?|prices?|subscription|subscribe|upgrade|billing|buy now)\b/i,
    textZh: /套餐|定价|价格|订阅|升级|购买|付费|价目/,
  },
  docs: {
    url: /(^|\/)(docs?|documentation|guide|guides|reference|api|api-?docs|developer|developers|learn|manual|wiki|how-?to|tutorial|examples?|sdk|quickstart|get-?started|handbook)(\/|$|-|_|\.)/i,
    // 刻意不含 learn / more：「Learn More」是营销按钮不是文档入口，实测 forethought 的
    // /channels 就被这个按钮骗成了文档页
    textEn: /\b(docs?|documentation|guide|guides|reference|api|developer|developers|manual|wiki|handbook|tutorial)\b/i,
    textZh: /文档|帮助中心|开发者|教程|上手|快速开始|使用说明|技术文档/,
  },
  terms: {
    // strong / weak 分级：命中 /legal/ 只能给弱分，否则「/legal/accessibility」会
    // 被当成服务条款（实测 ada 踩过）
    url: /(^|\/)(terms|tos|terms-of-service|terms-of-use|user-agreement|service-?terms|eula)(\/|$|-|_|\.)/i,
    urlWeak: /(^|\/)(legal|agreement)(\/|$|-|_|\.)/i,
    textEn: /\b(terms|tos|eula)\b/i,
    textZh: /服务条款|使用条款|条款|协议/,
  },
  privacy: {
    url: /(^|\/)(privacy|privacy-policy|data-protection|gdpr|cookie-policy)(\/|$|-|_|\.)/i,
    urlWeak: /(^|\/)(legal)(\/|$|-|_|\.)/i,
    textEn: /\b(privacy|gdpr)\b/i,
    textZh: /隐私|数据保护|个人信息保护/,
  },
  refund: {
    url: /(^|\/)(refund|refund-policy|returns?|return-policy|cancellation|cancel|money-?back)(\/|$|-|_|\.)/i,
    textEn: /\b(refund|refunds|returns?|cancellation|money-back)\b/i,
    textZh: /退款|退订|退货|退款政策/,
  },
  security: {
    url: /(^|\/)(security|trust|trust-center|compliance|soc2|soc-2|iso-27001|responsible-?disclosure)(\/|$|-|_|\.)/i,
    textEn: /\b(security|trust|trust\s?center|compliance)\b/i,
    textZh: /安全|合规/,
  },
  contact: {
    // 刻意不含 /about：公司简介页不是联系渠道（manus 的 /about 302 到 /unavailable）
    // 锚文本也只认明确的「联系我们」类措辞 —— 单靠 support 会把
    // 「language support page」这种产品页当成联系方式（qodo 踩过）
    url: /(^|\/)(contact|contact-us|support|help|feedback)(\/|$|-|_|\.)/i,
    textEn: /\b(contact|contact us)\b/i,
    textZh: /联系|客服|联系我们|意见反馈/,
  },
};

/* 明显不是「产品公开信息页」的链接：登录注册、文章与博客、案例研究、招聘、社媒、锚点。
   注意边界写法：blog/news 这些段后面直接跟 (\/|$|-) 才行；
   早期版本写成 'blog\/(\/|$)' —— 先吃掉斜杠再要求分隔符，结果 /blog/xxx 一条都过滤不掉，
   qodo 的两篇博客就被当成文档页和安全页计了分。 */
const JUNK_URL = new RegExp(
  '(^|/)(login|sign-?in|sign-?up|sign-?out|logout|register|join|cart|checkout|account|pricing-calculator|'
  + 'blog|news|newsroom|press|careers?|jobs?|events?|webinars?|newsletter|changelog|release-?notes|'
  + 'community|forum|customers?|case-stud\\w*|research|academy|partner\\w*|investors?|about-us|team|'
  // /learn-more 是通用 CTA，不算文档（实测 sierra 的 /learn-more 被算成文档中心）；
  // /use-case/、/solutions/ 是营销页，不算安全/合规页（synthesia 的合规培训用例页就这么中过）
  + 'learn-more|use-cases?|solutions?|industries|why-|compare|vs-|download|demo-request|'
  + 'discord|slack|x\\.com|twitter\\.com|facebook\\.com|linkedin\\.com|youtube\\.com|instagram\\.com|'
  + 'reddit\\.com|medium\\.com|zhihu\\.com|weibo\\.com|bilibili\\.com|tiktok\\.com)(/|$|-|_|\\.)', 'i');
const JUNK_FILE = /\.(pdf|zip|rar|exe|dmg|apk|jpg|jpeg|png|gif|svg|webp|mp4|csv|xlsx?|docx?|pptx?)$/i;

/* 子域名本身就是产品对页面性质的自我声明，比路径更可靠（docs.ada.cx 的路径只有 "/"）。
   权重给到 4，高于路径匹配（3）与锚文本匹配（2）。 */
const HOST_RULES = [
  { re: /^(docs|documentation|developer|developers|api|apidocs)\./i, cat: 'docs' },
  { re: /^(help|support)\./i, cat: 'docs' },
  { re: /^(trust|security|compliance)\./i, cat: 'security' },
  { re: /^(help|support)\./i, cat: 'contact' },
];

/* 通用 CTA 文案：这类锚文本不能当作分类依据，否则「Learn More」「Read more」
   会把任意落地页变成文档页 */
const CTA_ONLY = /^(learn more|read more|view (all|more|details)|see (all|more|how)|more|details|get started|try (it|now|free)|sign up|contact us|book a demo|watch demo)$/i;

const NOT_REPO = new Set(['marketplace', 'features', 'collections', 'topics', 'sponsors',
  'orgs', 'apps', 'settings', 'explore', 'trending', 'pricing', 'login', 'join',
  'about', 'site', 'contact', 'security', 'sponsors-explore', 'events', 'readme',
  'solutions', 'enterprise', 'team', 'resources', 'newsroom', 'customer-stories']);

function priceCheck(text) {
  const priceRe = /([\$€¥£]\s?\d[\d,\.]*)|(\d[\d,\.]*\s?(USD|CNY|EUR|GBP))/g;
  const periodRe = /(per month|\/mo\b|\/month|每月|per seat|annually|\/year|\/yr|monthly)/i;
  let m, hits = [];
  while ((m = priceRe.exec(text)) !== null) {
    const win = text.slice(Math.max(0, m.index - 150), m.index + 150);
    if (periodRe.test(win)) hits.push(m[0].trim());
    if (hits.length > 6) break;
  }
  return hits;
}

/**
 * 给单个 URL 打分类分。首页抓到的锚点和 sitemap 里的条目都走这里。
 * @returns {{cats: Object<string, number>, repo: string, skip: boolean}}
 */
function classifyUrl(rawUrl, text, baseReg, homePath) {
  const out = { cats: {}, repo: '', skip: false };
  let u;
  try { u = new URL(rawUrl); } catch (e) { out.skip = true; return out; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') { out.skip = true; return out; }
  const p = u.pathname.toLowerCase();
  const txt = String(text || '');

  // GitHub 仓库：产品自己声明的开源仓库
  if (/^(www\.)?github\.com$/i.test(u.host)) {
    const seg = p.split('/').filter(Boolean);
    if (seg.length >= 2 && !NOT_REPO.has(seg[0])) {
      out.repo = seg[0] + '/' + seg[1].replace(/\.git$/, '');
    }
    return out;
  }

  if (baseReg && regDomain(u.host) !== baseReg) { out.skip = true; return out; }
  if (JUNK_FILE.test(p) || JUNK_URL.test(p)) { out.skip = true; return out; }
  // 营销资源目录里的 guides/reports 不是文档中心（gong 的 Gartner 报告页就这么中过招）
  if (/^\/resources\/(guides|reports|ebooks|whitepapers|case-studies)\//i.test(p)) { out.skip = true; return out; }
  if (homePath && (p === homePath || p === homePath + '/' || p === '/' || p === '')) { out.skip = true; return out; }

  // 产品能力页 / 行业页里的 compliance 不是安全合规声明（CoCounsel 的税务合规页）
  const secBlocked = /\/(products|industries|onesource|use-cases?)\//i.test(p)
      && !/(security|trust-center|soc|iso-?27001|gdpr)/i.test(p);
  const hasCta = CTA_ONLY.test(txt.trim());

  for (const [cat, rule] of Object.entries(CATS)) {
    if (cat === 'security' && secBlocked) continue;
    let s = 0;
    if (rule.url.test(p)) s += 3;
    else if (rule.urlWeak && rule.urlWeak.test(p)) s += 1;
    if (!hasCta && (rule.textEn.test(txt) || rule.textZh.test(txt))) s += 2;
    if (s && cat === 'docs' && /\/faq(\/|$)/i.test(p)
        && !(rule.textEn.test(txt) || rule.textZh.test(txt))) s -= 2;
    if (s > 0) out.cats[cat] = s;
  }
  for (const hr of HOST_RULES) {
    if (!hr.re.test(u.host)) continue;
    out.cats[hr.cat] = Math.max(out.cats[hr.cat] || 0, 4 + (out.cats[hr.cat] ? 1 : 0));
  }
  return out;
}

/* ---------------------------------------------------------------------------
   sitemap 专用：末段精确匹配。
   sitemap 条目没有锚文本佐证，用上面的模糊规则会大面积误判——实测
   zencoder 的 /glossary/security-testing-in-software-testing（术语表文章）被当成安全页、
   vapi 的 /custom-agents/refund-agent（退款 Agent 产品页）被当成退款政策页、
   koreai 的 /whitepaper/guide-...（白皮书）被当成文档中心。
   所以 sitemap 场景只认「路径末段恰好等于标准页面名」的条目，宁可漏也不误判。
   --------------------------------------------------------------------------- */
const STRICT_TAIL = {
  pricing: ['pricing', 'plans', 'price', 'prices', 'plans-pricing', 'pricing-plans'],
  docs: ['docs', 'doc', 'documentation', 'api', 'api-docs', 'api-reference',
    'developer', 'developers', 'developer-docs', 'quickstart', 'getting-started'],
  terms: ['terms', 'tos', 'terms-of-service', 'terms-of-use', 'terms-and-conditions',
    'terms-conditions', 'eula', 'user-agreement', 'service-terms'],
  privacy: ['privacy', 'privacy-policy', 'privacy-statement', 'privacy-notice', 'cookie-policy'],
  refund: ['refund', 'refunds', 'refund-policy', 'refund-policy-2', 'cancellation',
    'cancellation-policy', 'return-policy', 'returns', 'money-back'],
  security: ['security', 'trust', 'trust-center', 'compliance', 'soc2', 'soc-2', 'iso-27001'],
  contact: ['contact', 'contact-us', 'support', 'help'],
};
// sitemap 里成堆出现的营销/内容目录，末段再像也不能算
const STRICT_JUNK = /(^|\/)(whitepapers?|glossary|ebook|ebooks|resources?|templates?|articles?|category|categories|tags?|authors?|topics?|comparisons?|alternatives|vs|best|reviews?|personas?|marketplace|integrations?|connectors?)(\/|$)/i;

/** sitemap 条目分类：只有末段精确命中才算，返回 {cat: score} */
function classifyStrict(rawUrl, baseReg) {
  const out = {};
  let u;
  try { u = new URL(rawUrl); } catch (e) { return out; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return out;
  if (baseReg && regDomain(u.host) !== baseReg) return out;
  const p = u.pathname.toLowerCase();
  if (JUNK_FILE.test(p)) return out;
  if (JUNK_URL.test(p)) return out;               // /blog/refund 这类内容页也不能算
  if (STRICT_JUNK.test(p)) return out;
  const segs = p.split('/').filter(Boolean);
  if (!segs.length || segs.length > 3) return out;      // 深层嵌套基本都是内容页
  const tail = segs[segs.length - 1].replace(/\.(html?|php|aspx)$/i, '');
  // 挂在 /product(s)/ /platform/ 下的多是产品能力页，不是安全/条款声明
  // （apollo.io 的 /product/security 就是能力介绍页）
  const capabilityPage = /\/(products?|platform|features?)(\/|$)/i.test(p);
  for (const [cat, names] of Object.entries(STRICT_TAIL)) {
    if (!names.includes(tail)) continue;
    if (capabilityPage && cat in { security: 1, privacy: 1, terms: 1, refund: 1 }) continue;
    out[cat] = 3;
    break;
  }
  return out;
}

module.exports = {
  MIN_SCORE, MULTI_TENANT, MULTI_SUFFIX, CATS, JUNK_URL, JUNK_FILE, HOST_RULES,
  CTA_ONLY, NOT_REPO, originOf, regDomain, priceCheck, classifyUrl,
  STRICT_TAIL, STRICT_JUNK, classifyStrict,
};
