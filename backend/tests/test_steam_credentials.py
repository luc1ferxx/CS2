import base64
import unittest

from app.services.steam_credentials import (
    EncryptedValue,
    SteamCredentialCipher,
    SteamCredentialError,
)

TEST_KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


class SteamCredentialCipherTest(unittest.TestCase):
    def test_encrypt_round_trip_is_randomized_and_bound_to_context(self) -> None:
        cipher = SteamCredentialCipher(TEST_KEY, "test-v1")

        first = cipher.encrypt(
            "credential-value",
            owner_id="owner_v1_test",
            purpose="game-auth-code",
            record_id="connection-a",
        )
        second = cipher.encrypt(
            "credential-value",
            owner_id="owner_v1_test",
            purpose="game-auth-code",
            record_id="connection-a",
        )

        self.assertNotEqual(first.nonce, second.nonce)
        self.assertNotEqual(first.ciphertext, second.ciphertext)
        self.assertEqual(first.key_version, "test-v1")
        self.assertEqual(
            cipher.decrypt(
                first,
                owner_id="owner_v1_test",
                purpose="game-auth-code",
                record_id="connection-a",
            ),
            "credential-value",
        )

    def test_aad_tampering_and_ciphertext_tampering_fail_closed(self) -> None:
        cipher = SteamCredentialCipher(TEST_KEY, "test-v1")
        encrypted = cipher.encrypt(
            "credential-value",
            owner_id="owner_v1_test",
            purpose="known-code",
            record_id="connection-a",
        )

        for context in (
            {
                "owner_id": "owner_v1_other",
                "purpose": "known-code",
                "record_id": "connection-a",
            },
            {
                "owner_id": "owner_v1_test",
                "purpose": "game-auth-code",
                "record_id": "connection-a",
            },
            {
                "owner_id": "owner_v1_test",
                "purpose": "known-code",
                "record_id": "connection-b",
            },
        ):
            with self.subTest(context=context), self.assertRaises(SteamCredentialError):
                cipher.decrypt(encrypted, **context)

        tampered = EncryptedValue(
            ciphertext=encrypted.ciphertext[:-1]
            + bytes([encrypted.ciphertext[-1] ^ 1]),
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        with self.assertRaises(SteamCredentialError):
            cipher.decrypt(
                tampered,
                owner_id="owner_v1_test",
                purpose="known-code",
                record_id="connection-a",
            )

        malformed_version = EncryptedValue(
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_version="bad version",
        )
        with self.assertRaises(SteamCredentialError):
            cipher.decrypt(
                malformed_version,
                owner_id="owner_v1_test",
                purpose="known-code",
                record_id="connection-a",
            )

    def test_key_version_and_key_shape_are_strict(self) -> None:
        cipher = SteamCredentialCipher(TEST_KEY, "test-v1")
        encrypted = cipher.encrypt(
            "credential-value",
            owner_id="owner_v1_test",
            purpose="known-code",
            record_id="connection-a",
        )

        wrong_version = SteamCredentialCipher(TEST_KEY, "test-v2")
        with self.assertRaisesRegex(SteamCredentialError, "version"):
            wrong_version.decrypt(
                encrypted,
                owner_id="owner_v1_test",
                purpose="known-code",
                record_id="connection-a",
            )

        for invalid_key in ("", "not-base64", base64.urlsafe_b64encode(b"short").decode()):
            with self.subTest(invalid_key=invalid_key), self.assertRaises(
                SteamCredentialError
            ):
                SteamCredentialCipher(invalid_key, "test-v1")

    def test_fingerprint_is_stable_without_exposing_the_value(self) -> None:
        fingerprint = SteamCredentialCipher.fingerprint("CSGO-abcde-fghij-klmno-pqrst-uvwxy")

        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(
            fingerprint,
            SteamCredentialCipher.fingerprint("CSGO-abcde-fghij-klmno-pqrst-uvwxy"),
        )
        self.assertNotIn("CSGO", fingerprint)


if __name__ == "__main__":
    unittest.main()
