"""
Focused tests for the P1 Groq-model-configuration fix in gemini_service.py.

Background: the Groq chat model was previously hardcoded as
"llama-3.1-8b-instant" in _call_groq(); Groq began rejecting it for this
project's account with "model ... does not exist or you do not have access
to it". The fix makes the model configurable via config.GROQ_MODEL
(env var GROQ_MODEL). An interim default of "llama-3.3-70b-versatile" was
tried and ALSO rejected with the same model_not_found error — a live,
read-only GET /v1/models call against this account's own key (2026-08-25)
confirmed the account has no Llama chat models enabled at all. The current
default, "openai/gpt-oss-20b", is a general-purpose (non-agentic) chat model
taken directly from that live, account-specific model list.

No real Groq API calls are made anywhere in this file — the Groq client is
always mocked.
"""
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_BACKEND_DIR = str(Path(__file__).resolve().parent.parent)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault("FLASK_ENV", "development")
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")

try:
    import services.gemini_service as gs
    from config import config as _config
    _IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover - environment-dependent
    gs = None
    _config = None
    _IMPORT_ERROR = _e


@unittest.skipIf(gs is None, f"services.gemini_service not importable: {_IMPORT_ERROR}")
class GroqModelConfigTestCase(unittest.TestCase):
    def setUp(self):
        # Every test gets a clean client singleton and a clean config value —
        # _get_client() caches a module-level _client, and config.GROQ_MODEL
        # is a class attribute shared across tests.
        gs._client = None
        self._orig_model = _config.GROQ_MODEL
        self._orig_key = _config.GROQ_API_KEY

    def tearDown(self):
        gs._client = None
        _config.GROQ_MODEL = self._orig_model
        _config.GROQ_API_KEY = self._orig_key

    def _mock_groq_client(self, response_json='{"genre": "House", "subgenre": "", "mood": "", "instruments": "", "energy": 0.5, "description": ""}', finish_reason="stop"):
        """A fake Groq client whose chat.completions.create() returns a
        minimal, well-formed chat completion, and records every call."""
        fake_response = MagicMock()
        fake_response.choices = [MagicMock()]
        fake_response.choices[0].message.content = response_json
        fake_response.choices[0].finish_reason = finish_reason
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response
        return fake_client

    # ── configured model is passed to Groq ───────────────────────────────
    def test_configured_model_is_passed_to_groq(self):
        _config.GROQ_MODEL = "custom-test-model-123"
        fake_client = self._mock_groq_client()
        with patch.object(gs, "_get_client", return_value=fake_client):
            result = gs._call_groq("Some Title", "Some Artist")
        fake_client.chat.completions.create.assert_called_once()
        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "custom-test-model-123")
        self.assertEqual(result["genre"], "House")

    def test_default_model_is_openai_gpt_oss_20b(self):
        # Exercise the real default-resolution path (env var genuinely unset)
        # in a clean subprocess — config.GROQ_MODEL is a class attribute
        # computed once at import time, so mutating this process's copy
        # can't tell us what a fresh process with no GROQ_MODEL sees.
        env = {k: v for k, v in os.environ.items() if k != "GROQ_MODEL"}
        env["FLASK_ENV"] = "development"
        env.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from config import config; print(config.GROQ_MODEL)" % _BACKEND_DIR],
            cwd=_BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "openai/gpt-oss-20b")

    # ── missing model configuration follows the intended default ────────
    def test_missing_groq_model_env_falls_back_to_default_end_to_end(self):
        """Same as above, but also proves _call_groq() actually uses
        whatever config.GROQ_MODEL resolves to — not a second hardcoded
        value that happens to coincide with the default."""
        _config.GROQ_MODEL = "openai/gpt-oss-20b"  # what a clean env resolves to (proven above)
        fake_client = self._mock_groq_client()
        with patch.object(gs, "_get_client", return_value=fake_client):
            gs._call_groq("Some Title", "Some Artist")
        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "openai/gpt-oss-20b")

    def test_groq_model_env_var_override_respected_end_to_end(self):
        env = {k: v for k, v in os.environ.items()}
        env["FLASK_ENV"] = "development"
        env.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
        env["GROQ_MODEL"] = "meta-llama/llama-4-scout-17b-16e-instruct"
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from config import config; print(config.GROQ_MODEL)" % _BACKEND_DIR],
            cwd=_BACKEND_DIR, env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "meta-llama/llama-4-scout-17b-16e-instruct")

    # ── token-budget fix for reasoning models (2026-08-25) ───────────────
    def test_max_completion_tokens_600_not_deprecated_max_tokens(self):
        """openai/gpt-oss-20b (this project's default) is a reasoning model —
        reasoning tokens count against the completion budget, and 200 was
        observed truncating real responses (finish_reason=length). The fix
        raised the budget to 600 via the non-deprecated max_completion_tokens
        param — lock in both the value and that the old param name is gone."""
        fake_client = self._mock_groq_client()
        with patch.object(gs, "_get_client", return_value=fake_client):
            gs._call_groq("Some Title", "Some Artist")
        _, kwargs = fake_client.chat.completions.create.call_args
        self.assertEqual(kwargs.get("max_completion_tokens"), 600)
        self.assertNotIn("max_tokens", kwargs)

    def test_truncated_response_logs_warning(self):
        """finish_reason="length" (budget exceeded despite the raised limit)
        must be logged with enough context to diagnose — not just surfaced as
        a generic downstream JSON-decode error."""
        fake_client = self._mock_groq_client(response_json='{"genre": "Punjabi", "subge', finish_reason="length")
        captured = io.StringIO()
        from loguru import logger as _loguru_logger
        sink_id = _loguru_logger.add(captured, level="DEBUG")
        try:
            with patch.object(gs, "_get_client", return_value=fake_client), \
                 patch.object(gs, "_read_tags", return_value={"title": "T", "artist": "A", "genre": ""}):
                result = gs.analyze_audio("/fake/path.mp3")
        finally:
            _loguru_logger.remove(sink_id)
        log_text = captured.getvalue()
        self.assertIn("truncated", log_text)
        self.assertIn("finish_reason=length", log_text)
        # And the pre-existing degrade-safely contract still holds — a
        # truncated, unparseable response must not raise, just return {}.
        self.assertEqual(result, {})

    def test_complete_response_does_not_log_truncation_warning(self):
        """Sanity check: finish_reason="stop" (the normal case) must not
        trigger the truncation warning — avoids false-positive log noise."""
        fake_client = self._mock_groq_client(finish_reason="stop")
        captured = io.StringIO()
        from loguru import logger as _loguru_logger
        sink_id = _loguru_logger.add(captured, level="DEBUG")
        try:
            with patch.object(gs, "_get_client", return_value=fake_client):
                gs._call_groq("Some Title", "Some Artist")
        finally:
            _loguru_logger.remove(sink_id)
        self.assertNotIn("truncated", captured.getvalue())

    # ── AI call failure still degrades safely ────────────────────────────
    def test_model_not_found_style_error_degrades_to_empty_dict(self):
        """Reproduces the exact reported failure shape (a model-access
        error from the Groq client) and confirms analyze_audio()/
        identify_audio() still return {} rather than raising — the
        documented, pre-existing degrade-safely contract must survive
        this change unchanged."""
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError(
            "The model `llama-3.1-8b-instant` does not exist or you do not have access to it."
        )
        with patch.object(gs, "_get_client", return_value=fake_client), \
             patch.object(gs, "_read_tags", return_value={"title": "T", "artist": "A", "genre": ""}):
            self.assertEqual(gs.analyze_audio("/fake/path.mp3"), {})
            self.assertEqual(gs.identify_audio("/fake/path.mp3"), {})

    def test_no_id3_tags_still_returns_empty_dict_without_calling_groq(self):
        with patch.object(gs, "_read_tags", return_value={"title": "", "artist": "", "genre": ""}):
            with patch.object(gs, "_get_client") as mock_get_client:
                self.assertEqual(gs.analyze_audio("/fake/path.mp3"), {})
                mock_get_client.assert_not_called()

    # ── no API key is exposed in logs ─────────────────────────────────────
    def test_api_key_never_appears_in_logs_on_failure(self):
        secret = "gsk_test_super_secret_value_should_never_leak_9f8e7d"
        _config.GROQ_API_KEY = secret

        captured = io.StringIO()
        try:
            from loguru import logger as _loguru_logger
            sink_id = _loguru_logger.add(captured, level="DEBUG")
        except ImportError:
            self.skipTest("loguru not available in this environment")

        try:
            fake_client = MagicMock()
            # Simulate an HTTP-client-style exception that (worst case)
            # echoes request context into its string representation.
            fake_client.chat.completions.create.side_effect = RuntimeError(
                f"401 Unauthorized (key ending ...{secret[-6:]})"
            )
            with patch.object(gs, "_get_client", return_value=fake_client), \
                 patch.object(gs, "_read_tags", return_value={"title": "T", "artist": "A", "genre": ""}):
                gs.analyze_audio("/fake/path.mp3")
                gs.identify_audio("/fake/path.mp3")
        finally:
            _loguru_logger.remove(sink_id)

        log_text = captured.getvalue()
        self.assertNotIn(secret, log_text, "full API key must never reach the logs")

    def test_client_init_error_message_does_not_contain_key(self):
        """_get_client() itself must never format the raw key into a log
        line or exception message beyond what's necessary."""
        _config.GROQ_API_KEY = ""
        gs._client = None
        with self.assertRaises(RuntimeError) as ctx:
            gs._get_client()
        self.assertNotIn("gsk_", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
