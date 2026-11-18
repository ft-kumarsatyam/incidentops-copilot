// Mark the action finished, only after the issue exists, so a failed run stays retryable.
const DONE_TTL_SECONDS = 7 * 24 * 3600;
const gate = $('Action Idempotency Gate').first().json;
const key = 'action:' + String(gate.idempotency_key);
if (gate.store === 'redis') {
  await redis(String($env.N8N_REDIS_URL), [['SET', 'incidentops:n8n:' + key, 'done:' + new Date().toISOString(), 'EX', DONE_TTL_SECONDS]]);
} else {
  const seen = $getWorkflowStaticData('global');
  seen[key] = {...(seen[key] || {}), done:true, completed_at:new Date().toISOString()};
}
return [{json:{...gate,duplicate:false,execute:true}}];
