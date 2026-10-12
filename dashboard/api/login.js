const { MAX_AGE, makeSession, sessionCookie, safeEqual, json, repos, readBody } = require('./_lib');

module.exports = async (req, res) => {
  if (req.method !== 'POST') return json(res, 405, { error: 'استخدم POST' });
  if (!process.env.DASHBOARD_PASSWORD || !process.env.SESSION_SECRET) {
    return json(res, 500, { error: 'كلمة السر غير مضبوطة على الخادم' });
  }
  let password = '';
  try {
    password = String(JSON.parse(await readBody(req) || '{}').password || '');
  } catch {
    return json(res, 400, { error: 'طلب غير صالح' });
  }
  if (!password || !safeEqual(password, process.env.DASHBOARD_PASSWORD)) {
    await new Promise(r => setTimeout(r, 1200)); // إبطاء محاولات التخمين
    return json(res, 401, { error: 'كلمة السر غير صحيحة' });
  }
  res.setHeader('Set-Cookie', sessionCookie(makeSession(), MAX_AGE));
  return json(res, 200, { ok: true, ...repos() });
};
