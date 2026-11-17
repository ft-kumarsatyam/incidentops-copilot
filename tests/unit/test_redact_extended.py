import pytest

from incidentops.core.redact import REDACTED, redact, redact_text


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111"),
        ("card 4111-1111-1111-1111", "4111-1111-1111-1111"),
        ("amex 378282246310005", "378282246310005"),
        ("ssn 123-45-6789", "123-45-6789"),
        ("db postgres://admin:s3cr3t-Pa55@db.internal:5432/x", "s3cr3t-Pa55"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "MIIabc"),
        ("-----BEGIN PRIVATE KEY-----\nMIIabc\n(truncated paste)", "MIIabc"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop", "eyJhbGciOiJ"),
        ("AIza" + "A" * 35, "AIzaAAAA"),
        # Built at runtime so GitHub push protection does not treat the fixture as a live Stripe key.
        ("stripe " + "sk_live_" + "a" * 24, "sk_live_" + "a" * 6),
        ("Authorization: Basic dXNlcjpwYXNzd29yZA==", "dXNlcjpwYXNzd29yZA"),
        ("sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "sk-ant-api03"),
        ("pass=hunter2", "hunter2"),
        ("pwd: hunter2", "hunter2"),
        ("client_secret=abc123def", "abc123def"),
        ("xoxb-1234567890-abcdefghij", "xoxb-1234"),
    ],
)
def test_secret_is_removed(text: str, secret: str) -> None:
    assert secret not in redact_text(text)


@pytest.mark.parametrize(
    "text",
    [
        "Checkout API is returning elevated 5xx responses",
        "thread 1700000000.0001 in C-INCIDENTS",
        "epoch ms 1700000000000 and 1700000000123",
        "commit abcdef1234567890abcdef1234567890abcdef12",
        "ticket INC-4111111111111",
        "version 4.11.1111.1111",
        "latency 2400ms at 12% error rate",
        "the token is expired",
        "uuid 123e4567-e89b-12d3-a456-426614174000",
    ],
)
def test_normal_operational_text_survives(text: str) -> None:
    assert redact_text(text) == text


def test_luhn_failing_digit_runs_are_not_treated_as_cards() -> None:
    assert redact_text("id 4111 1111 1111 1112") == "id 4111 1111 1111 1112"


def test_redaction_is_idempotent() -> None:
    once = redact_text("token=abc ghp_abcdefghijklmnopqrstuvwxyz0123 a@b.co 4111 1111 1111 1111")

    assert redact_text(once) == once


def test_sensitive_keys_are_masked_at_any_depth_and_in_lists() -> None:
    cleaned = redact(
        {
            "a": [{"cookie": "session=abc", "ok": 1}],
            "b": {"private_key": "xyz", "card_number": "4111"},
        }
    )

    assert cleaned["a"][0]["cookie"] == REDACTED and cleaned["a"][0]["ok"] == 1
    assert cleaned["b"] == {"private_key": REDACTED, "card_number": REDACTED}


def test_non_string_values_pass_through() -> None:
    assert redact({"n": 1, "f": 0.5, "b": True, "none": None, "t": ["x", 2]}) == {
        "n": 1,
        "f": 0.5,
        "b": True,
        "none": None,
        "t": ["x", 2],
    }


@pytest.mark.parametrize(
    "shape",
    ["A", "1", "1 ", "a.", "a@", "a://", "-", "token ", "eyJ" + "a"],
)
def test_redaction_runs_in_linear_time_on_hostile_input(shape: str) -> None:
    """Redaction runs on untrusted text before any length cap, so it must not be quadratic.

    A quadratic pattern needs minutes on 250 KB; the generous bound keeps this stable on CI.
    """
    import time

    text = shape * (250_000 // len(shape))
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 3.0
