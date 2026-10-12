const { isAuthed, json, repos } = require('./_lib');

module.exports = async (req, res) => {
  const authenticated = isAuthed(req);
  return json(res, 200, authenticated ? { authenticated, ...repos() } : { authenticated });
};
