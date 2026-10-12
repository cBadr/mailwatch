// وسيط محدود إلى GitHub API: يضيف التوكن المحفوظ على الخادم بعد التحقق من الجلسة،
// ويسمح فقط بالعمليات التي تحتاجها لوحة التحكم على المستودعين المحددين.
const { isAuthed, json, repos, readBody } = require('./_lib');

const ACCEPT = new Set(['application/vnd.github+json', 'application/vnd.github.raw+json']);

function allowed(method, repo, sub, r) {
  if (repo === r.data) {
    if (method === 'GET' && (sub === '' || sub.startsWith('contents/'))) return true;
    if (method === 'PUT' && sub.startsWith('contents/')) return true;
  }
  if (repo === r.code) {
    if (method === 'GET' && sub === '') return true;
    if (method === 'POST' && (sub === 'dispatches' || /^actions\/workflows\/[\w.-]+\.ya?ml\/dispatches$/.test(sub))) return true;
  }
  return false;
}

module.exports = async (req, res) => {
  if (!isAuthed(req)) return json(res, 401, { message: 'سجّل الدخول أولًا' });
  // ترويسة مخصصة تمنع الطلبات القادمة من مواقع أخرى (تحتاج CORS غير مسموح به)
  if (req.headers['x-mw'] !== '1') return json(res, 403, { message: 'طلب غير مسموح' });
  if (!process.env.GITHUB_TOKEN) return json(res, 500, { message: 'GITHUB_TOKEN غير مضبوط على الخادم' });

  const raw = String(req.query.p || '');
  const [pathPart, query = ''] = raw.split('?', 2);
  if (!pathPart || pathPart.includes('..') || pathPart.includes('//')) return json(res, 403, { message: 'مسار غير مسموح' });
  const m = pathPart.match(/^repos\/([^/]+\/[^/]+)(?:\/(.*))?$/);
  if (!m) return json(res, 403, { message: 'مسار غير مسموح' });
  const [, repo, sub = ''] = m;
  const method = req.method;
  if (!allowed(method, repo, sub, repos())) return json(res, 403, { message: 'عملية غير مسموحة' });

  const download = req.query.dl === '1' && method === 'GET' && sub.startsWith('contents/');
  const accept = download ? 'application/vnd.github+json'
    : (ACCEPT.has(req.headers.accept) ? req.headers.accept : 'application/vnd.github+json');
  const headers = {
    Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
    Accept: accept,
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'mailwatch-dashboard',
  };
  if (req.headers['if-none-match'] && !download) headers['If-None-Match'] = req.headers['if-none-match'];
  let body;
  if (method === 'PUT' || method === 'POST') {
    body = await readBody(req);
    headers['Content-Type'] = 'application/json';
  }

  const url = `https://api.github.com/${pathPart}${query ? `?${query}` : ''}`;
  let r;
  try {
    r = await fetch(url, { method, headers, body });
  } catch (e) {
    return json(res, 502, { message: `تعذر الاتصال بـ GitHub: ${e.message}` });
  }

  if (download) {
    // المرفقات الكبيرة لا تمر عبر الخادم (حد الرد 4.5MB): نعيد رابط تنزيل مؤقتًا من GitHub
    const meta = await r.json().catch(() => ({}));
    if (!r.ok) return json(res, r.status, { message: meta.message || 'تعذر جلب الملف' });
    return json(res, 200, { download_url: meta.download_url || null, size: meta.size || 0 });
  }

  res.statusCode = r.status;
  for (const h of ['content-type', 'etag']) {
    const v = r.headers.get(h);
    if (v) res.setHeader(h, v);
  }
  res.setHeader('Cache-Control', 'no-store');
  res.end(Buffer.from(await r.arrayBuffer()));
};
