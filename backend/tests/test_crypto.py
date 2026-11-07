import pytest

from app.core.crypto import decrypt_secret, encrypt_secret


def test_encrypt_secret_returns_different_ciphertext_for_same_plaintext():
    plaintext = "my-secret-token"
    encrypted1 = encrypt_secret(plaintext)
    encrypted2 = encrypt_secret(plaintext)
    assert encrypted1 != encrypted2, "Different encryptions of same plaintext should produce different ciphertexts"


def test_encrypt_then_decrypt_returns_original_string():
    plaintext = "my-sensitive-oauth-token-12345"
    encrypted = encrypt_secret(plaintext)
    decrypted = decrypt_secret(encrypted)
    assert decrypted == plaintext


def test_encrypt_different_plaintexts_produce_different_ciphertexts():
    plaintext1 = "token1"
    plaintext2 = "token2"
    encrypted1 = encrypt_secret(plaintext1)
    encrypted2 = encrypt_secret(plaintext2)
    assert encrypted1 != encrypted2


def test_decrypt_invalid_ciphertext_raises_error():
    from cryptography.fernet import InvalidToken

    invalid_ciphertext = "not-a-valid-fernet-token"
    with pytest.raises(InvalidToken):
        decrypt_secret(invalid_ciphertext)
