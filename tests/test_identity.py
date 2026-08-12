"""Tests for client.identity — key lifecycle and challenge signatures."""

import stat
import sys

import pytest

from client import identity
from client.identity import IdentityError


class TestGenerate:
    def test_generates_rsa_2048(self):
        private_key, public_key = identity.generate_identity()
        assert private_key.key_size == 2048
        assert public_key.key_size == 2048

    def test_fingerprint_stable_and_distinct(self):
        k1, p1 = identity.generate_identity()
        k2, p2 = identity.generate_identity()
        fp1a = identity.public_key_fingerprint(p1)
        fp1b = identity.public_key_fingerprint(p1)
        assert fp1a == fp1b
        assert fp1a != identity.public_key_fingerprint(p2)


class TestSaveLoad:
    def test_roundtrip(self, identity_dir):
        private_key, public_key = identity.generate_identity()
        private_path, public_path = identity.save_identity(
            private_key, public_key, path=identity_dir
        )
        assert private_path.exists()
        assert public_path.exists()

        loaded_private, loaded_public = identity.load_identity(path=identity_dir)
        assert loaded_private.key_size == 2048
        # The loaded key must be usable: sign with one, verify with the other
        signature = loaded_private.sign(b"hello", identity.padding.PKCS1v15(), identity.hashes.SHA256())
        loaded_public.verify(signature, b"hello", identity.padding.PKCS1v15(), identity.hashes.SHA256())
        assert identity.public_key_fingerprint(loaded_public) == identity.public_key_fingerprint(public_key)

    @pytest.mark.skipif(
        sys.platform == "win32", reason="POSIX permission bits don't apply on Windows"
    )
    def test_private_key_permissions_0600(self, identity_dir):
        private_key, public_key = identity.generate_identity()
        private_path, _ = identity.save_identity(
            private_key, public_key, path=identity_dir
        )
        mode = stat.S_IMODE(private_path.stat().st_mode)
        # Owner read/write allowed; group/other must not read
        assert mode & stat.S_IRUSR
        assert mode & stat.S_IWUSR
        assert not (mode & (stat.S_IRGRP | stat.S_IROTH))

    def test_missing_identity_raises(self, tmp_path):
        with pytest.raises(IdentityError):
            identity.load_identity(path=tmp_path / "nope")

    def test_corrupt_private_key_raises(self, identity_dir):
        private_key, public_key = identity.generate_identity()
        identity.save_identity(private_key, public_key, path=identity_dir)
        (identity_dir / identity.PRIVATE_KEY_FILENAME).write_text("garbage")
        with pytest.raises(IdentityError):
            identity.load_identity(path=identity_dir)

    def test_load_public_key_from_directory(self, identity_dir):
        private_key, public_key = identity.generate_identity()
        identity.save_identity(private_key, public_key, path=identity_dir)
        loaded = identity.load_public_key(str(identity_dir))
        assert identity.public_key_fingerprint(loaded) == identity.public_key_fingerprint(public_key)

    def test_default_dir_uses_tilde(self):
        assert identity.DEFAULT_IDENTITY_DIR == "~/.localnetwork"


class TestChallenge:
    def test_sign_verify_roundtrip(self):
        private_key, public_key = identity.generate_identity()
        challenge = b"nonce-12345"
        signature = identity.sign_challenge(private_key, challenge)
        assert identity.verify_challenge(public_key, challenge, signature) is True

    def test_tampered_signature_rejected(self):
        private_key, public_key = identity.generate_identity()
        signature = identity.sign_challenge(private_key, b"challenge")
        tampered = bytes([signature[0] ^ 0xFF]) + signature[1:]
        assert identity.verify_challenge(public_key, b"challenge", tampered) is False

    def test_wrong_challenge_rejected(self):
        private_key, public_key = identity.generate_identity()
        signature = identity.sign_challenge(private_key, b"real-challenge")
        assert identity.verify_challenge(public_key, b"other-challenge", signature) is False

    def test_wrong_public_key_rejected(self):
        private_key, public_key = identity.generate_identity()
        other_private, other_public = identity.generate_identity()
        signature = identity.sign_challenge(private_key, b"challenge")
        assert identity.verify_challenge(other_public, b"challenge", signature) is False

    def test_garbage_signature_returns_false(self):
        _, public_key = identity.generate_identity()
        assert identity.verify_challenge(public_key, b"c", b"\x00" * 64) is False

    def test_empty_challenge_rejected(self):
        private_key, _ = identity.generate_identity()
        with pytest.raises(IdentityError):
            identity.sign_challenge(private_key, b"")

    def test_verify_never_raises_on_bad_input(self):
        _, public_key = identity.generate_identity()
        assert identity.verify_challenge(public_key, b"c", b"\xff" * 256) is False
