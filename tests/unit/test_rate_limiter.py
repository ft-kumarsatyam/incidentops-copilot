from incidentops.api.ratelimit import RateLimiter


def test_blocks_after_the_limit_and_recovers_after_the_window() -> None:
    limiter = RateLimiter(2)

    assert limiter.allow("c", now=0)
    assert limiter.allow("c", now=1)
    assert not limiter.allow("c", now=2)
    assert limiter.allow("other", now=2)
    assert limiter.allow("c", now=61.5)


def test_zero_disables_limiting() -> None:
    limiter = RateLimiter(0)
    assert all(limiter.allow("c", now=0) for _ in range(1000))
