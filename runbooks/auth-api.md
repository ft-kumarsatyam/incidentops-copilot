# Authentication API incident runbook

## Sign-in outage

Check the identity-provider status, token-signing key health, and authentication
error classes. Separate invalid-credential failures from server errors before
raising severity.

## Safe mitigation

Disable a newly introduced authentication path only through the approved
feature flag. Never rotate signing keys during an incident without the identity
owner and incident commander present.

## Verification

Test one existing session, one new sign-in, and one token refresh. Monitor
server-error rate and authentication latency for fifteen minutes.
