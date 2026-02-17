"""Ed25519 signature generation for Revolut X API authentication."""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class Ed25519Signer:
    """Handles Ed25519 signature generation for API requests.

    The Revolut X API requires Ed25519 signatures for authentication.
    The signature is generated from a concatenation of:
    Timestamp + HTTP Method + Request Path + Query String + Request Body
    """

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        """Initialize the signer with an Ed25519 private key.

        Args:
            private_key: The Ed25519 private key for signing requests.
        """
        self.private_key = private_key

    def generate_signature(
        self,
        timestamp: str,
        method: str,
        path: str,
        query: str,
        body: str,
    ) -> str:
        """Generate an Ed25519 signature for an API request.

        Args:
            timestamp: Unix timestamp in milliseconds as string.
            method: HTTP method (GET, POST, etc.).
            path: API endpoint path.
            query: URL query string (without leading ?).
            body: Request body as JSON string.

        Returns:
            Hexadecimal signature string (128 characters).
        """
        # Concatenate all components in the required order
        message = f"{timestamp}{method}{path}{query}{body}"

        # Sign the message
        signature_bytes: bytes = self.private_key.sign(message.encode("utf-8"))

        # Return as hex string
        return signature_bytes.hex()

    @staticmethod
    def load_private_key_from_file(key_path: str) -> Ed25519PrivateKey:
        """Load an Ed25519 private key from a PEM file.

        Args:
            key_path: Path to the PEM-encoded private key file.

        Returns:
            The loaded Ed25519PrivateKey object.

        Raises:
            FileNotFoundError: If the key file doesn't exist.
            ValueError: If the file doesn't contain a valid Ed25519 key.
        """
        path = Path(key_path)
        if not path.exists():
            raise FileNotFoundError(f"Private key file not found: {key_path}")

        key_data = path.read_bytes()

        private_key = serialization.load_pem_private_key(
            key_data,
            password=None,
        )

        if not isinstance(private_key, Ed25519PrivateKey):
            raise ValueError("The provided key is not an Ed25519 private key")

        return private_key
