const { sessionCookie, json } = require('./_lib');

module.exports = async (req, res) => {
  if (req.method !== 'POST') return json(res, 405, { error: 'استخدم POST' });
  res.setHeader('Set-Cookie', sessionCookie('', 0));
  return json(res, 200, { ok: true });
};
