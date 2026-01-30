"""Tests for Polymarket wallet and signing."""

import os
import pytest

from src.polymarket.wallet import PolymarketWallet, PolymarketCredentials
from src.polymarket.exceptions import PolymarketAuthError


# Test private key (DO NOT USE IN PRODUCTION)
# This is a well-known test key from hardhat/foundry
TEST_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TEST_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


class TestPolymarketWallet:
    """Tests for PolymarketWallet."""

    def test_wallet_initialization(self):
        """Test wallet initialization with private key."""
        wallet = PolymarketWallet(TEST_PRIVATE_KEY)

        assert wallet.address.lower() == TEST_ADDRESS.lower()

    def test_wallet_without_0x_prefix(self):
        """Test wallet with key without 0x prefix."""
        key_without_prefix = TEST_PRIVATE_KEY[2:]
        wallet = PolymarketWallet(key_without_prefix)

        assert wallet.address.lower() == TEST_ADDRESS.lower()

    def test_wallet_invalid_key_length(self):
        """Test wallet with invalid key length."""
        with pytest.raises(PolymarketAuthError, match="Invalid private key length"):
            PolymarketWallet("0x1234")

    def test_wallet_invalid_key_format(self):
        """Test wallet with invalid key format."""
        with pytest.raises(PolymarketAuthError, match="Invalid private key format"):
            PolymarketWallet("0x" + "zz" * 32)

    def test_wallet_from_env_var(self, monkeypatch):
        """Test wallet initialization from environment variable."""
        monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", TEST_PRIVATE_KEY)

        wallet = PolymarketWallet()
        assert wallet.address.lower() == TEST_ADDRESS.lower()

    def test_wallet_missing_env_var(self, monkeypatch):
        """Test wallet initialization without env var."""
        monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)

        with pytest.raises(PolymarketAuthError, match="Private key not provided"):
            PolymarketWallet()

    def test_create_l1_headers(self):
        """Test L1 authentication header creation."""
        wallet = PolymarketWallet(TEST_PRIVATE_KEY)
        headers = wallet.create_l1_headers(nonce=1234567890)

        assert "POLY_ADDRESS" in headers
        assert "POLY_SIGNATURE" in headers
        assert "POLY_TIMESTAMP" in headers
        assert headers["POLY_ADDRESS"] == wallet.address
        assert headers["POLY_TIMESTAMP"] == "1234567890"

    def test_create_l2_headers(self):
        """Test L2 authentication header creation."""
        wallet = PolymarketWallet(TEST_PRIVATE_KEY)
        headers = wallet.create_l2_headers(
            method="GET",
            path="/orders",
            body=None,
            nonce=1234567890,
        )

        assert "POLY_ADDRESS" in headers
        assert "POLY_SIGNATURE" in headers
        assert headers["POLY_ADDRESS"] == wallet.address

    def test_create_l2_headers_with_body(self):
        """Test L2 headers with request body."""
        wallet = PolymarketWallet(TEST_PRIVATE_KEY)
        body = '{"order": "test"}'

        headers = wallet.create_l2_headers(
            method="POST",
            path="/order",
            body=body,
            nonce=1234567890,
        )

        assert "POLY_SIGNATURE" in headers
        # Signature should be different with body
        headers_no_body = wallet.create_l2_headers(
            method="POST",
            path="/order",
            body=None,
            nonce=1234567890,
        )
        assert headers["POLY_SIGNATURE"] != headers_no_body["POLY_SIGNATURE"]

    def test_derive_api_key(self):
        """Test API key derivation."""
        wallet = PolymarketWallet(TEST_PRIVATE_KEY)
        api_key = wallet.derive_api_key()

        assert len(api_key) == 32
        # Should be deterministic
        assert api_key == wallet.derive_api_key()


class TestPolymarketCredentials:
    """Tests for PolymarketCredentials."""

    def test_credentials_with_wallet(self):
        """Test credentials with wallet."""
        wallet = PolymarketWallet(TEST_PRIVATE_KEY)
        creds = PolymarketCredentials(wallet=wallet)

        headers = creds.get_headers("GET", "/markets")
        assert "POLY_ADDRESS" in headers

    def test_credentials_with_api_key(self, monkeypatch):
        """Test credentials with API key."""
        monkeypatch.setenv("POLYMARKET_API_KEY", "test_key")
        monkeypatch.setenv("POLYMARKET_API_SECRET", "test_secret")

        creds = PolymarketCredentials()

        headers = creds.get_headers("GET", "/markets")
        assert "POLY_API_KEY" in headers
        assert headers["POLY_API_KEY"] == "test_key"

    def test_credentials_no_auth(self, monkeypatch):
        """Test credentials without any auth."""
        monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("POLYMARKET_API_KEY", raising=False)
        monkeypatch.delenv("POLYMARKET_API_SECRET", raising=False)

        creds = PolymarketCredentials()

        with pytest.raises(PolymarketAuthError, match="No valid credentials"):
            creds.get_headers("GET", "/markets")
