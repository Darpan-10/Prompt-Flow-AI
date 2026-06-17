#!/usr/bin/env python3
"""
Generate RS256 (RSA) key pair for JWT signing.
Run once: python scripts/generate_keys.py
"""
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from pathlib import Path

def generate_keys():
    keys_dir = Path("keys")
    keys_dir.mkdir(exist_ok=True)
    
    private_key_path = keys_dir / "private.pem"
    public_key_path = keys_dir / "public.pem"
    
    if private_key_path.exists() and public_key_path.exists():
        print("✓ Keys already exist at keys/private.pem and keys/public.pem")
        return
    
    print("Generating 4096-bit RSA key pair...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=4096,
        backend=default_backend(),
    )
    
    # Save private key
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_key_path.write_bytes(private_pem)
    print(f"✓ Private key saved to {private_key_path}")
    
    # Save public key
    public_key = private_key.public_key()
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_path.write_bytes(public_pem)
    print(f"✓ Public key saved to {public_key_path}")
    print("\n✓ Keys generated successfully!")
    print("Update your .env: JWT_PRIVATE_KEY_PATH=keys/private.pem")

if __name__ == "__main__":
    generate_keys()
