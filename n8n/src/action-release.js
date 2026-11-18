// A step failed after the claim was taken: give the claim back (only if it is still ours), then
// fail so the caller retries and the action can run again.
const gate = $('Action Idempotency Gate').first().json;
if (gate.store === 'redis') {
  const script = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end";
  try {
    await redis(String($env.N8N_REDIS_URL), [['EVAL', script, 1, 'incidentops:n8n:action:' + String(gate.idempotency_key), 'claim:' + gate.claim]]);
  } catch (e) { /* Redis down: the lease expires on its own */ }
}
const why = $input.first().json.error;
throw new Error('action failed, claim released: ' + String((why && (why.message || why)) || 'unknown'));
