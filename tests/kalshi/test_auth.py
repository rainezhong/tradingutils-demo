"""Tests for Kalshi authentication."""

import unittest
from unittest.mock import patch

from src.kalshi.auth import (
    KalshiAuth,
    generate_auth_headers,
    generate_signature,
)


class TestGenerateSignature(unittest.TestCase):
    """Tests for signature generation."""

    def test_signature_format(self):
        """Test signature is base64 encoded."""
        sig = generate_signature(
            api_secret="test-secret",
            timestamp_ms=1704067200000,
            method="GET",
            path="/trade-api/v2/markets",
        )
        # Should be valid base64
        import base64
        try:
            base64.b64decode(sig)
        except Exception:
            self.fail("Signature is not valid base64")

    def test_signature_deterministic(self):
        """Test same inputs produce same signature."""
        sig1 = generate_signature(
            api_secret="secret",
            timestamp_ms=1000,
            method="GET",
            path="/test",
        )
        sig2 = generate_signature(
            api_secret="secret",
            timestamp_ms=1000,
            method="GET",
            path="/test",
        )
        self.assertEqual(sig1, sig2)

    def test_signature_varies_with_secret(self):
        """Test different secrets produce different signatures."""
        sig1 = generate_signature(
            api_secret="secret1",
            timestamp_ms=1000,
            method="GET",
            path="/test",
        )
        sig2 = generate_signature(
            api_secret="secret2",
            timestamp_ms=1000,
            method="GET",
            path="/test",
        )
        self.assertNotEqual(sig1, sig2)

    def test_signature_varies_with_timestamp(self):
        """Test different timestamps produce different signatures."""
        sig1 = generate_signature(
            api_secret="secret",
            timestamp_ms=1000,
            method="GET",
            path="/test",
        )
        sig2 = generate_signature(
            api_secret="secret",
            timestamp_ms=2000,
            method="GET",
            path="/test",
        )
        self.assertNotEqual(sig1, sig2)

    def test_signature_includes_body(self):
        """Test body is included in signature."""
        sig1 = generate_signature(
            api_secret="secret",
            timestamp_ms=1000,
            method="POST",
            path="/test",
            body="",
        )
        sig2 = generate_signature(
            api_secret="secret",
            timestamp_ms=1000,
            method="POST",
            path="/test",
            body='{"key": "value"}',
        )
        self.assertNotEqual(sig1, sig2)


class TestGenerateAuthHeaders(unittest.TestCase):
    """Tests for auth header generation."""

    def test_headers_contain_required_keys(self):
        """Test all required headers are present."""
        headers = generate_auth_headers(
            api_key="key123",
            api_secret="secret",
            method="GET",
            path="/test",
        )

        self.assertIn("KALSHI-ACCESS-KEY", headers)
        self.assertIn("KALSHI-ACCESS-SIGNATURE", headers)
        self.assertIn("KALSHI-ACCESS-TIMESTAMP", headers)

    def test_api_key_in_headers(self):
        """Test API key is in headers."""
        headers = generate_auth_headers(
            api_key="my-api-key",
            api_secret="secret",
            method="GET",
            path="/test",
        )
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "my-api-key")

    def test_custom_timestamp(self):
        """Test custom timestamp is used."""
        headers = generate_auth_headers(
            api_key="key",
            api_secret="secret",
            method="GET",
            path="/test",
            timestamp_ms=1234567890000,
        )
        self.assertEqual(headers["KALSHI-ACCESS-TIMESTAMP"], "1234567890000")


class TestKalshiAuth(unittest.TestCase):
    """Tests for KalshiAuth class."""

    def test_init(self):
        """Test initialization."""
        auth = KalshiAuth("key", "secret")
        self.assertEqual(auth.api_key, "key")

    def test_sign_request(self):
        """Test sign_request generates headers."""
        auth = KalshiAuth("key", "secret")
        headers = auth.sign_request("GET", "/test")

        self.assertIn("KALSHI-ACCESS-KEY", headers)
        self.assertEqual(headers["KALSHI-ACCESS-KEY"], "key")

    @patch.dict("os.environ", {"KALSHI_API_KEY": "env-key", "KALSHI_API_SECRET": "env-secret"})
    def test_from_env(self):
        """Test from_env creates instance from environment."""
        auth = KalshiAuth.from_env()
        self.assertEqual(auth.api_key, "env-key")

    @patch.dict("os.environ", {}, clear=True)
    def test_from_env_missing_key(self):
        """Test from_env raises on missing key."""
        with self.assertRaises(ValueError) as context:
            KalshiAuth.from_env()
        self.assertIn("KALSHI_API_KEY", str(context.exception))

    @patch.dict("os.environ", {"KALSHI_API_KEY": "key"}, clear=True)
    def test_from_env_missing_secret(self):
        """Test from_env raises on missing secret."""
        with self.assertRaises(ValueError) as context:
            KalshiAuth.from_env()
        self.assertIn("KALSHI_API_SECRET", str(context.exception))


if __name__ == "__main__":
    unittest.main()
