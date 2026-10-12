// أدوات مشتركة لدوال Vercel: الجلسة الموقّعة، المقارنة الآمنة، والردود.
// المتغيرات المطلوبة على Vercel:
//   DASHBOARD_PASSWORD  كلمة سر لوحة التحكم
//   SESSION_SECRET      سر عشوائي لتوقيع الجلسات
//   GITHUB_TOKEN        توكن GitHub (Contents + Actions) على مستودعي البيانات والكود
//   DATA_REPO / CODE_REPO  (اختياري) أسماء المستودعين
const crypto = require('crypto');

const COOKIE = 'mw_session';
const MAX_AGE = 30 * 24 * 3600; // 30 يومًا

function signingKey() {
  const secret = process.env.SESSION_SECRET || '';
  const password = process.env.DASHBOARD_PASSWORD || '';
  if (!secret || !password) throw new Error('SESSION_SECRET / DASHBOARD_PASSWORD غير مضبوطة');
  // تغيير كلمة السر يُبطل كل الجلسات القديمة تلقائيًا
  return crypto.createHash('sha256').update(`${secret}\0${password}`).digest();
}

function sign(exp) {
  return crypto.createHmac('sha256', signingKey()).update(`v1.${exp}`).digest('base64url');
}

function makeSession() {
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE;
  return `v1.${exp}.${sign(exp)}`;
}

function parseCookies(req) {
  const out = {};
  for (const part of (req.headers.cookie || '').split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function isAuthed(req) {
  try {
    const value = parseCookies(req)[COOKIE];
    if (!value) return false;
    const [ver, exp, sig] = value.split('.');
    if (ver !== 'v1' || !exp || !sig || Number(exp) < Date.now() / 1000) return false;
    const good = Buffer.from(sign(exp));
    const got = Buffer.from(sig);
    return good.length === got.length && crypto.timingSafeEqual(good, got);
  } catch {
    return false;
  }
}

function sessionCookie(value, maxAge) {
  return `${COOKIE}=${value}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}

function safeEqual(a, b) {
  const ha = crypto.createHash('sha256').update(String(a)).digest();
  const hb = crypto.createHash('sha256').update(String(b)).digest();
  return crypto.timingSafeEqual(ha, hb);
}

function json(res, status, obj) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(obj));
}

function repos() {
  return { data: process.env.DATA_REPO || 'cBadr/Hamza', code: process.env.CODE_REPO || 'cBadr/mailwatch' };
}

async function readBody(req) {
  if (req.body !== undefined && req.body !== null) {
    if (typeof req.body === 'string') return req.body;
    if (Buffer.isBuffer(req.body)) return req.body.toString('utf8');
    return JSON.stringify(req.body);
  }
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks).toString('utf8');
}

module.exports = { COOKIE, MAX_AGE, makeSession, isAuthed, sessionCookie, safeEqual, json, repos, readBody };
