"""Tests for client.encryption — ECDH key agreement and AES-256-GCM."""

import pytest

from client import encryption
from client.encryption import (
    CipherContext,
    DecryptionError,
    EncryptionError,
    SESSION_KEY_LENGTH,
)


class TestCipherContext:
    def test_encrypt_decrypt_roundtrip(self):
        ctx = CipherContext(b"\x11" * SESSION_KEY_LENGTH)
        plaintext = b"hello tunnel \x00\xff"
        ciphertext, tag = ctx.encrypt(plaintext)
        assert ciphertext != plaintext
        assert len(tag) == 16
        assert ctx.decrypt(ciphertext, tag) == plaintext

    def test_roundtrip_with_associated_data(self):
        ctx = CipherContext(b"\x22" * SESSION_KEY_LENGTH)
        ad = b"service_id+stream_id"
        ciphertext, tag = ctx.encrypt(b"payload", associated_data=ad)
        assert ctx.decrypt(ciphertext, tag, associated_data=ad) == b"payload"

    def test_wrong_key_fails(self):
        ctx_a = CipherContext(b"\x11" * SESSION_KEY_LENGTH)
        ctx_b = CipherContext(b"\x22" * SESSION_KEY_LENGTH)
        ciphertext, tag = ctx_a.encrypt(b"secret")
        with pytest.raises(DecryptionError):
            ctx_b.decrypt(ciphertext, tag)

    def test_tampered_ciphertext_fails(self):
        ctx = CipherContext(b"\x33" * SESSION_KEY_LENGTH)
        ciphertext, tag = ctx.encrypt(b"secret data")
        tampered = bytearray(ciphertext)
        tampered[15] ^= 0x01
        with pytest.raises(DecryptionError):
            ctx.decrypt(bytes(tampered), tag)

    def test_tampered_tag_fails(self):
        ctx = CipherContext(b"\x44" * SESSION_KEY_LENGTH)
        ciphertext, tag = ctx.encrypt(b"secret data")
        with pytest.raises(DecryptionError):
            ctx.decrypt(ciphertext, bytes([tag[0] ^ 0xFF]) + tag[1:])

    def test_wrong_associated_data_fails(self):
        ctx = CipherContext(b"\x55" * SESSION_KEY_LENGTH)
        ciphertext, tag = ctx.encrypt(b"payload", associated_data=b"ad-1")
        with pytest.raises(DecryptionError):
            ctx.decrypt(ciphertext, tag, associated_data=b"ad-2")

    def test_short_ciphertext_raises(self):
        ctx = CipherContext(b"\x66" * SESSION_KEY_LENGTH)
        with pytest.raises(DecryptionError):
            ctx.decrypt(b"\x00\x01", b"\x00" * 16)

    def test_bad_key_length_rejected(self):
        with pytest.raises(EncryptionError):
            CipherContext(b"too-short")

    def test_nonces_are_unique(self):
        ctx = CipherContext(b"\x77" * SESSION_KEY_LENGTH)
        c1, _ = ctx.encrypt(b"same")
        c2, _ = ctx.encrypt(b"same")
        assert c1[:12] != c2[:12]  # random nonces

    def test_encrypt_full_self_contained(self):
        ctx = CipherContext(b"\x88" * SESSION_KEY_LENGTH)
        blob = ctx.encrypt_full(b"data")
        assert len(blob) == 12 + 4 + 16  # nonce + body + tag
        # decrypt() expects the nonce-prefixed ciphertext plus the tag
        assert ctx.decrypt(blob[:-16], blob[-16:]) == b"data"
        assert blob[:12] != blob[12:-16]  # nonce differs from body


class TestEcdh:
    def test_both_sides_derive_same_key(self):
        alice_priv = encryption.generate_ecdh_keypair()
        bob_priv = encryption.generate_ecdh_keypair()
        alice_key = encryption.derive_session_key(
            alice_priv, bob_priv.public_key()
        )
        bob_key = encryption.derive_session_key(bob_priv, alice_priv.public_key())
        assert alice_key == bob_key
        assert len(alice_key) == SESSION_KEY_LENGTH

    def test_different_keypairs_different_keys(self):
        a1 = encryption.generate_ecdh_keypair()
        b1 = encryption.generate_ecdh_keypair()
        a2 = encryption.generate_ecdh_keypair()
        b2 = encryption.generate_ecdh_keypair()
        k1 = encryption.derive_session_key(a1, b1.public_key())
        k2 = encryption.derive_session_key(a2, b2.public_key())
        assert k1 != k2

    def test_public_bytes_roundtrip(self):
        priv = encryption.generate_ecdh_keypair()
        raw = encryption.ecdh_public_bytes(priv)
        assert len(raw) == 32
        pub = encryption.ecdh_public_from_bytes(raw)
        other = encryption.generate_ecdh_keypair()
        k1 = encryption.derive_session_key(other, pub)
        k2 = encryption.derive_session_key_from_bytes(other, raw)
        assert k1 == k2

    def test_bad_public_key_length_rejected(self):
        with pytest.raises(EncryptionError):
            encryption.ecdh_public_from_bytes(b"\x00" * 31)

    def test_shared_key_end_to_end_encryption(self):
        """Full flow: derive keys on both sides, encrypt/decrypt across."""
        a_priv = encryption.generate_ecdh_keypair()
        b_priv = encryption.generate_ecdh_keypair()
        a_key = encryption.derive_session_key(a_priv, b_priv.public_key())
        b_key = encryption.derive_session_key(b_priv, a_priv.public_key())

        a_ctx = CipherContext(a_key)
        b_ctx = CipherContext(b_key)
        ciphertext, tag = a_ctx.encrypt(b"ping", associated_data=b"peer-a")
        assert b_ctx.decrypt(ciphertext, tag, associated_data=b"peer-a") == b"ping"

    def test_key_is_32_bytes(self):
        a = encryption.generate_ecdh_keypair()
        b = encryption.generate_ecdh_keypair()
        assert len(encryption.derive_session_key(a, b.public_key())) == 32
