from src.infrastructure.services.model_settings import profile


def test_curated_profile_does_not_enable_speculation_or_overwrite_user():
    user = {"context_length": 16384, "temperature": 0.4, "mtp": "on"}
    result = profile("bonsai-27b-q1", 8192, configured=user)
    assert result["suggested"]["context_length"] == 8192
    assert result["suggested"]["mtp"] == "off"
    assert result["configured"] == user
    assert result["effective"] == {}
    assert result["status"] == "suggested"


def test_unknown_model_preserves_server_settings_without_guesses():
    result = profile("fictional/model", configured={"context_length": 4096})
    assert result["suggested"] == {}
    assert result["status"] == "server-managed"


def test_readiness_identity_changes_with_runtime_and_overrides():
    original = profile("qwen3.5-9b")
    assert original["fingerprint"] != profile("qwen3.5-9b", effective={"context_length": 4096})["fingerprint"]
    assert original["fingerprint"] != profile("qwen3.5-9b", provider="ollama")["fingerprint"]
