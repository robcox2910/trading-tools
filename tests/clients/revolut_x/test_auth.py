"""Tests for Revolut X authentication."""

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from trading_tools.clients.revolut_x.auth.signer import Ed25519Signer

ED25519_SIGNATURE_B64_LENGTH = 88


class TestEd25519Signer:
    """Test suite for Ed25519 signature generation."""

    @pytest.fixture
    def private_key(self) -> Ed25519PrivateKey:
        """Generate a test Ed25519 private key."""
        return Ed25519PrivateKey.generate()

    @pytest.fixture
    def signer(self, private_key: Ed25519PrivateKey) -> Ed25519Signer:
        """Create an Ed25519Signer instance."""
        return Ed25519Signer(private_key)

    def test_signer_initialization(self, private_key: Ed25519PrivateKey) -> None:
        """Test that signer can be initialized with a private key."""
        signer = Ed25519Signer(private_key)
        assert signer is not None

    def test_generate_signature(self, signer: Ed25519Signer) -> None:
        """Test signature generation for a message."""
        timestamp = "1640000000000"
        method = "GET"
        path = "/api/1.0/orders"
        query = ""
        body = ""

        signature = signer.generate_signature(timestamp, method, path, query, body)

        # Signature should be a base64 string (88 characters for Ed25519)
        assert isinstance(signature, str)
        assert len(signature) == ED25519_SIGNATURE_B64_LENGTH

    def test_signature_with_query_string(self, signer: Ed25519Signer) -> None:
        """Test signature generation with query parameters."""
        timestamp = "1640000000000"
        method = "GET"
        path = "/api/1.0/orders"
        query = "status=open&limit=10"
        body = ""

        signature = signer.generate_signature(timestamp, method, path, query, body)

        assert isinstance(signature, str)
        assert len(signature) == ED25519_SIGNATURE_B64_LENGTH

    def test_signature_with_body(self, signer: Ed25519Signer) -> None:
        """Test signature generation with request body."""
        timestamp = "1640000000000"
        method = "POST"
        path = "/api/1.0/orders"
        query = ""
        body = '{"symbol":"BTC-USD","side":"buy","quantity":"0.1"}'

        signature = signer.generate_signature(timestamp, method, path, query, body)

        assert isinstance(signature, str)
        assert len(signature) == ED25519_SIGNATURE_B64_LENGTH

    def test_signature_consistency(self, signer: Ed25519Signer) -> None:
        """Test that the same inputs produce the same signature."""
        timestamp = "1640000000000"
        method = "GET"
        path = "/api/1.0/balance"
        query = ""
        body = ""

        sig1 = signer.generate_signature(timestamp, method, path, query, body)
        sig2 = signer.generate_signature(timestamp, method, path, query, body)

        assert sig1 == sig2

    def test_different_timestamps_produce_different_signatures(self, signer: Ed25519Signer) -> None:
        """Test that different timestamps produce different signatures."""
        method = "GET"
        path = "/api/1.0/balance"
        query = ""
        body = ""

        sig1 = signer.generate_signature("1640000000000", method, path, query, body)
        sig2 = signer.generate_signature("1640000000001", method, path, query, body)

        assert sig1 != sig2

    def test_load_private_key_from_pem(self, tmp_path: Path) -> None:
        """Test loading a private key from PEM file."""
        # Generate a test key and save it
        key = Ed25519PrivateKey.generate()

        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        key_file = tmp_path / "test_key.pem"
        key_file.write_bytes(pem)

        # Load it back
        loaded_key = Ed25519Signer.load_private_key_from_file(str(key_file))
        assert loaded_key is not None

    def test_load_nonexistent_key_raises_error(self) -> None:
        """Test that loading a nonexistent key file raises an error."""
        with pytest.raises(FileNotFoundError):
            Ed25519Signer.load_private_key_from_file("/path/that/does/not/exist.pem")
