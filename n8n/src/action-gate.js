// Claim an approved action before running it. Redis when N8N_REDIS_URL is set (shared by every
// n8n instance); workflow static data otherwise (one instance only).
const LEASE_SECONDS = 600;
const b = $json.body ?? $json;
const idem = String(b.idempotency_key ?? '');
if (!idem) throw new Error('idempotency_key required');
const key = 'action:' + idem;
const url = String($env.N8N_REDIS_URL ?? '');
if (!url) {
  const seen = $getWorkflowStaticData('global');
  const duplicate = Boolean(seen[key] && seen[key].done);
  if (!duplicate) seen[key] = {run_id:b.run_id, at:new Date().toISOString(), done:false};
  return [{json:{...b,duplicate,execute:!duplicate,store:'static'}}];
}
const rkey = 'incidentops:n8n:' + key;
const token = Date.now().toString(36) + Math.random().toString(36).slice(2);
const [claimed] = await redis(url, [['SET', rkey, 'claim:' + token, 'NX', 'EX', LEASE_SECONDS]]);
if (claimed === 'OK') return [{json:{...b,duplicate:false,execute:true,store:'redis',claim:token}}];
const [current] = await redis(url, [['GET', rkey]]);
if (typeof current === 'string' && current.startsWith('done:')) {
  return [{json:{...b,duplicate:true,execute:false,store:'redis'}}];
}
// Another instance holds the claim and has not finished. Failing makes the caller retry later,
// instead of answering "duplicate" for an action that may still fail.
throw new Error('action already in progress on another instance');
