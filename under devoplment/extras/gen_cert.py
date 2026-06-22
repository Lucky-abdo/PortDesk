import socket
import os
import ipaddress
import datetime

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# Try to use flexible BASE_DIR from pd_config if available
try:
    import pd_config as _pd_config
    BASE_DIR = _pd_config.BASE_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Detect primary LAN IP
local_ip = "127.0.0.1"
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
except OSError:
    local_ip = "127.0.0.1"

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PortDesk"),
])

_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

_san = [
    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    x509.DNSName("localhost"),
]
try:
    _lan = ipaddress.ip_address(local_ip)
    if not _lan.is_loopback:
        _san.insert(0, x509.IPAddress(_lan))
except ValueError:
    pass

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(_now)
    .not_valid_after(_now + datetime.timedelta(days=90))
    .add_extension(x509.SubjectAlternativeName(_san), critical=False)
    .sign(key, hashes.SHA256())
)

cert_path = os.path.join(BASE_DIR, "cert.pem")
key_path = os.path.join(BASE_DIR, "key.pem")

with open(cert_path, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

with open(key_path, "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()
    ))

try:
    os.chmod(key_path, 0o600)
except OSError:
    pass

print(f"✅ Certificate generated successfully:")
print(f"   cert.pem → {cert_path}")
print(f"   key.pem  → {key_path}")
print(f"   IP: {local_ip}")
print(f"\n📱 On mobile browser:")
print(f"   Open https://{local_ip}:5000")
print(f"   Click 'Advanced' → 'Proceed'")