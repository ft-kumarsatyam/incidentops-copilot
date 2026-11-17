# Checkout API incident runbook

## Elevated 5xx error rate

Confirm the error-rate alert across at least two five-minute windows. Compare
the start time with the latest checkout deployment. Check payment-provider
health before changing application infrastructure.

## Safe mitigation

If failures began immediately after a deploy and the previous release is known
good, prepare a rollback. The incident commander must approve the rollback.
Do not retry customer charges manually.

## Verification

After mitigation, confirm checkout success rate, duplicate-charge count, and
p95 latency for fifteen minutes. Post the evidence links in the incident thread.
