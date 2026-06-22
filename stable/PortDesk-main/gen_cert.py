import socket, os, datetime
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    local_ip = s.getsockname()[0]
    s.close()
except:
    local_ip = "127.0.0.1"

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COMMON_NAME, local_ip),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PCC"),
])

cert = (
    x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([
        x509.IPAddress(__import__('ipaddress').ip_address(local_ip)),
        x509.IPAddress(__import__('ipaddress').ip_address('127.0.0.1')),
    ]), critical=False)
    .sign(key, hashes.SHA256())
)

cert_path = os.path.join(BASE_DIR, "cert.pem")
key_path  = os.path.join(BASE_DIR, "key.pem")

with open(cert_path, "wb") as f:
    f.write(cert.public_bytes(serialization.Encoding.PEM))

with open(key_path, "wb") as f:
    f.write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()
    ))

print(f"✅ تم إنشاء الشهادة:")
print(f"   cert.pem → {cert_path}")
print(f"   key.pem  → {key_path}")
print(f"   IP: {local_ip}")
print(f"\n📱 على الموبايل:")
print(f"   1. افتح https://{local_ip}:5000 في المتصفح")
print(f"   2. اضغط 'Advanced' → 'Proceed'")
print(f"   أو ثبّت cert.pem يدوياً: Settings → Security → Install certificate")