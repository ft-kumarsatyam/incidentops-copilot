// Minimal Redis client over a raw socket (RESP2). n8n's own Redis node has no SET NX, and a Code
// node cannot hold credentials, so the idempotency gate speaks to Redis directly.
// Needs NODE_FUNCTION_ALLOW_BUILTIN=net,tls on the n8n process. URL: redis[s]://[user:pass@]host:port/db
const net = require('net');
const tls = require('tls');
function redisConfig(raw) {
  // No URL global in n8n's Code-node sandbox, so parse by hand.
  const m = /^(rediss?):\/\/(?:([^:@\/]*)(?::([^@\/]*))?@)?(\[[^\]]+\]|[^:\/?#]+)(?::(\d+))?(?:\/(\d+))?/.exec(String(raw));
  if (!m) throw new Error('N8N_REDIS_URL must look like redis://[user:pass@]host:port/db');
  return { secure: m[1] === 'rediss', host: m[4].replace(/^\[|\]$/g, ''), port: Number(m[5] || 6379),
    user: decodeURIComponent(m[2] || ''), pass: decodeURIComponent(m[3] || ''), db: Number(m[6] || 0) };
}
function redisEncode(args) {
  const parts = [Buffer.from('*' + args.length + '\r\n')];
  for (const arg of args) {
    const b = Buffer.from(String(arg), 'utf8');
    parts.push(Buffer.from('$' + b.length + '\r\n'), b, Buffer.from('\r\n'));
  }
  return Buffer.concat(parts);
}
function redisParse(buf, at) {
  const end = buf.indexOf('\r\n', at, 'latin1');
  if (at >= buf.length || end < 0) return null;
  const kind = String.fromCharCode(buf[at]);
  const line = buf.toString('latin1', at + 1, end);
  if (kind === '+') return [line, end + 2];
  if (kind === '-') return [new Error(line), end + 2];
  if (kind === ':') return [Number(line), end + 2];
  if (kind === '$') {
    const n = Number(line);
    if (n < 0) return [null, end + 2];
    if (buf.length < end + 2 + n + 2) return null;
    return [buf.toString('utf8', end + 2, end + 2 + n), end + 2 + n + 2];
  }
  throw new Error('unsupported redis reply type ' + kind);
}
function redis(rawUrl, commands, timeoutMs = 3000) {
  const cfg = redisConfig(rawUrl);
  const pre = [];
  if (cfg.pass) pre.push(cfg.user ? ['AUTH', cfg.user, cfg.pass] : ['AUTH', cfg.pass]);
  if (cfg.db) pre.push(['SELECT', cfg.db]);
  const all = pre.concat(commands);
  return new Promise((resolve, reject) => {
    const socket = (cfg.secure ? tls : net).connect(
      cfg.secure ? { host: cfg.host, port: cfg.port, servername: cfg.host } : { host: cfg.host, port: cfg.port });
    let buf = Buffer.alloc(0);
    const replies = [];
    let settled = false;
    const finish = (err, value) => {
      if (settled) return;
      settled = true; clearTimeout(timer); socket.destroy();
      if (err) reject(err); else resolve(value);
    };
    const timer = setTimeout(() => finish(new Error('redis timeout')), timeoutMs);
    socket.on('error', finish);
    socket.write(Buffer.concat(all.map(redisEncode)));
    socket.on('data', (chunk) => {
      buf = Buffer.concat([buf, chunk]);
      try {
        let at = 0, parsed;
        while (replies.length < all.length && (parsed = redisParse(buf, at))) { replies.push(parsed[0]); at = parsed[1]; }
        buf = buf.subarray(at);
      } catch (e) { return finish(e); }
      if (replies.length < all.length) return;
      const failed = replies.find((r) => r instanceof Error);
      if (failed) return finish(failed);
      finish(null, replies.slice(pre.length));
    });
  });
}
