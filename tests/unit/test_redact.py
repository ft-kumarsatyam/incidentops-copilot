from incidentops.core.redact import REDACTED, redact, redact_text


def test_redacts_common_secret_formats() -> None:
    text = "token ghp_abcdefghijklmnopqrstuvwxyz0123 and AKIAABCDEFGHIJKLMNOP via a@b.co"
    cleaned = redact_text(text)

    assert "ghp_" not in cleaned
    assert "AKIA" not in cleaned
    assert "a@b.co" not in cleaned


def test_redacts_key_value_pairs_and_bearer_tokens() -> None:
    assert redact_text("password=hunter2 ok") == f"password={REDACTED} ok"
    assert "abcdefghijklmnopqrstuvwxyz" not in redact_text("Bearer abcdefghijklmnopqrstuvwxyz")


def test_redacts_nested_structures_and_sensitive_keys() -> None:
    cleaned = redact({"github": {"token": "xyz", "repo": "acme/x"}, "notes": ["mail me@x.io"]})

    assert cleaned["github"]["token"] == REDACTED
    assert cleaned["github"]["repo"] == "acme/x"
    assert "me@x.io" not in cleaned["notes"][0]


def test_leaves_normal_incident_text_untouched() -> None:
    text = "Checkout API is returning elevated 5xx responses"
    assert redact_text(text) == text
