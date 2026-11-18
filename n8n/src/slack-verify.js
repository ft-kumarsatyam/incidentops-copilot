// Verify Slack's request signature (https://api.slack.com/authentication/verifying-requests-from-slack).
// Opt-in: with no SLACK_SIGNING_SECRET the request passes through, like the API's inbound signing.
// Once a secret is set, a missing, stale or wrong signature is rejected. Pure JS HMAC-SHA256 so
// the Code node needs no allowed builtins.
const K = new Uint32Array([
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
function sha256(data) {
  const H = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
  const size = ((data.length + 9 + 63) >> 6) << 6;
  const m = Buffer.alloc(size);
  data.copy(m); m[data.length] = 0x80;
  m.writeUInt32BE(Math.floor(data.length / 0x20000000), size - 8);
  m.writeUInt32BE((data.length << 3) >>> 0, size - 4);
  const w = new Uint32Array(64);
  for (let off = 0; off < size; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = m.readUInt32BE(off + 4 * i);
    for (let i = 16; i < 64; i++) {
      const x = w[i - 15], y = w[i - 2];
      const s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
      const s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = ((e >>> 6) | (e << 26)) ^ ((e >>> 11) | (e << 21)) ^ ((e >>> 25) | (e << 7));
      const t1 = (h + S1 + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
      const S0 = ((a >>> 2) | (a << 30)) ^ ((a >>> 13) | (a << 19)) ^ ((a >>> 22) | (a << 10));
      const t2 = (S0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
  }
  const out = Buffer.alloc(32);
  for (let i = 0; i < 8; i++) out.writeUInt32BE(H[i], 4 * i);
  return out;
}
function hmacSha256Hex(secret, message) {
  let key = Buffer.from(secret, 'utf8');
  if (key.length > 64) key = sha256(key);
  const pad = Buffer.alloc(64); key.copy(pad);
  const inner = Buffer.alloc(64), outer = Buffer.alloc(64);
  for (let i = 0; i < 64; i++) { inner[i] = pad[i] ^ 0x36; outer[i] = pad[i] ^ 0x5c; }
  return sha256(Buffer.concat([outer, sha256(Buffer.concat([inner, message]))])).toString('hex');
}
const item = $input.first();
const secret = String($env.SLACK_SIGNING_SECRET ?? '');
if (!secret) return [item];
const headers = Object.fromEntries(Object.entries(item.json.headers ?? {}).map(([k, v]) => [k.toLowerCase(), String(v)]));
const stamp = headers['x-slack-request-timestamp'] ?? '';
if (!/^\d{1,12}$/.test(stamp) || Math.abs(Date.now() / 1000 - Number(stamp)) > 300) {
  throw new Error('slack signature: missing or stale timestamp');
}
const raw = await this.helpers.getBinaryDataBuffer(0, 'data');
const expected = 'v0=' + hmacSha256Hex(secret, Buffer.concat([Buffer.from('v0:' + stamp + ':'), raw]));
const given = headers['x-slack-signature'] ?? '';
let diff = given.length ^ expected.length;
for (let i = 0; i < expected.length; i++) diff |= (given.charCodeAt(i) || 0) ^ expected.charCodeAt(i);
if (diff !== 0) throw new Error('slack signature: invalid');
return [item];
