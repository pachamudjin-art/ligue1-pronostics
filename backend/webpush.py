"""
Implémentation Web Push (RFC 8291) sans dépendance externe.
Utilise uniquement la librairie 'cryptography' déjà installée.
"""
import base64
import json
import os
import struct
import time
import urllib.request
import urllib.error
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes, hmac as crypto_hmac
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend


def _b64d(s: str) -> bytes:
    """Décode base64 URL-safe sans padding."""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def _make_jwt(claims: dict, private_key_b64: str) -> str:
    """Crée un JWT ES256 pour VAPID."""
    header = _b64e(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    payload = _b64e(json.dumps(claims).encode())
    signing_input = f"{header}.{payload}".encode()

    # Reconstruire la clé privée
    priv_bytes = _b64d(private_key_b64)
    private_key = ec.derive_private_key(
        int.from_bytes(priv_bytes, "big"),
        ec.SECP256R1(),
        default_backend()
    )
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives import hashes as h
    der_sig = private_key.sign(signing_input, ec.ECDSA(h.SHA256()))
    r, s = decode_dss_signature(der_sig)
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{header}.{payload}.{_b64e(sig)}"

def _encrypt_payload(plaintext: bytes, sub_pub_key_b64: str, auth_b64: str) -> tuple:
    """Chiffre le payload selon RFC 8291 (aes128gcm)."""
    # Clé publique du subscriber
    sub_pub_bytes = _b64d(sub_pub_key_b64)
    sub_pub_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sub_pub_bytes)

    # Générer une paire de clés éphémère
    eph_private = ec.generate_private_key(ec.SECP256R1(), default_backend())
    eph_public = eph_private.public_key()
    eph_pub_bytes = eph_public.public_bytes(
        encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.X962,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PublicFormat"]).PublicFormat.UncompressedPoint
    )

    # ECDH
    shared_secret = eph_private.exchange(ec.ECDH(), sub_pub_key)

    # Salt
    salt = os.urandom(16)

    auth_secret = _b64d(auth_b64)

    # HKDF extract + expand (RFC 8291)
    # PRK_key = HMAC-SHA256(auth_secret, ecdh_secret)
    from cryptography.hazmat.primitives import hmac as hmac_mod
    h = hmac_mod.HMAC(auth_secret, hashes.SHA256(), default_backend())
    h.update(shared_secret)
    prk_key = h.finalize()

    # key_info = "WebPush: info\x00" + sub_pub + eph_pub
    key_info = b"WebPush: info\x00" + sub_pub_bytes + eph_pub_bytes
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=prk_key, info=key_info, backend=default_backend())
    ikm = hkdf.derive(b"")

    # PRK_cek = HMAC-SHA256(salt, ikm)
    h2 = hmac_mod.HMAC(salt, hashes.SHA256(), default_backend())
    h2.update(ikm)
    prk_cek = h2.finalize()

    # CEK
    cek_info = b"Content-Encoding: aes128gcm\x00"
    hkdf2 = HKDF(algorithm=hashes.SHA256(), length=16, salt=prk_cek, info=cek_info, backend=default_backend())
    cek = hkdf2.derive(b"")

    # NONCE
    nonce_info = b"Content-Encoding: nonce\x00"
    hkdf3 = HKDF(algorithm=hashes.SHA256(), length=12, salt=prk_cek, info=nonce_info, backend=default_backend())
    nonce = hkdf3.derive(b"")

    # Chiffrer avec padding
    padded = plaintext + b"\x02"  # delimiter
    aesgcm = AESGCM(cek)
    ciphertext = aesgcm.encrypt(nonce, padded, None)

    # Header aes128gcm
    record_size = len(ciphertext) + 16
    header = salt + struct.pack(">I", record_size) + bytes([len(eph_pub_bytes)]) + eph_pub_bytes

    return header + ciphertext, eph_pub_bytes

def send_web_push(endpoint: str, p256dh: str, auth: str,
                  title: str, body: str,
                  vapid_private_key: str, vapid_public_key: str,
                  vapid_subject: str = "mailto:pronos.ente.va@gmail.com") -> bool:
    """Envoie une notification Web Push."""
    try:
        # Payload JSON
        payload = json.dumps({"title": title, "body": body}).encode("utf-8")

        # Chiffrer
        encrypted, eph_pub = _encrypt_payload(payload, p256dh, auth)

        # Audience (origin de l'endpoint)
        from urllib.parse import urlparse
        parsed = urlparse(endpoint)
        audience = f"{parsed.scheme}://{parsed.netloc}"

        # JWT VAPID
        exp = int(time.time()) + 12 * 3600
        jwt = _make_jwt({"aud": audience, "exp": exp, "sub": vapid_subject}, vapid_private_key)

        # Headers
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "Authorization": f"vapid t={jwt},k={vapid_public_key}",
            "TTL": "86400",
            "Urgency": "normal",
        }

        req = urllib.request.Request(endpoint, data=encrypted, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            return status in (200, 201, 202)

    except urllib.error.HTTPError as e:
        print(f"Push HTTP error {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"Push error: {type(e).__name__}: {e}")
        return False
