# Privacy Policy

Last Updated: April 2026

## 1. Executive Summary: Zero-Data Architecture

PortDesk is engineered on a Zero-Data collection principle. The software operates as a localized environment where all data processing, transmission, and storage are confined strictly to the user's private infrastructure.

There are no cloud dependencies, no telemetry, and no third-party relays.

**PortDesk supports two connectivity modes:**

- **Local Mode (LAN):** All traffic remains within your local network. The server binds to your local interface (`0.0.0.0:5000`) and is only accessible to devices on the same network.
- **Remote Mode (WAN/Internet):** When you expose your server to the internet (via port forwarding, reverse proxy, or public IP), devices can connect from anywhere worldwide. **All remote connections are encrypted end-to-end via HTTPS/TLS** — no data passes through developer-owned or third-party servers.

> **Security Notice:** Exposing PortDesk to the internet increases your attack surface. We strongly recommend using a strong PIN, IP Whitelisting, and HTTPS when enabling remote access.

---

## 2. Data Processing & Storage

PortDesk facilitates real-time interaction between your devices. Whether over LAN or the internet

| Data Category | Processing Scope | Persistence |
| :--- | :--- | :--- |
| Input (Mouse/Keyboard) | Volatile Memory (RAM) Only | None |
| Media (Screen/Audio) | Volatile Memory (RAM) Only | None |
| File Metadata | Local Machine Only | None |
| Security (PIN/Whitelist) | Encrypted Local JSON / LocalStorage | Local Device Only |
| System Logs | Local `.log` Files | Local Device Only |

> **Key Point:** Even in Remote Mode, your screen data, keyboard input, and files never pass through a relay or cloud service. The connection is **directly peer-to-peer** between your client and your server.

---

## 3. Network & Security Architecture

PortDesk utilizes a **Direct-to-Peer (D2P)** connection model to ensure maximum privacy:

### 3.1 Local Scope (LAN)
* The server binds to your local interface (`0.0.0.0:5000`) and remains inaccessible from the public internet unless you explicitly configure port forwarding or a reverse proxy.
* Network discovery is handled via internal UDP checks to resolve local IP addresses without data transmission.

### 3.2 Remote Scope (WAN / Internet)
* When port forwarding or a public IP is configured, the server accepts connections from the global internet.
* **All remote traffic is protected by HTTPS/TLS encryption.** We strongly discourage using HTTP over the internet.
* **No data is routed through developer servers.** The connection flows directly: `Your Client → Your Router → Your Server`.
* You are responsible for securing your network perimeter (firewall rules, VPN, reverse proxy, etc.).

### 3.3 Authentication
* Access is governed by a Hardware-Specific Whitelist and an optional bcrypt-Hashed PIN, both stored exclusively on your host machine.
* Certificate-based authentication (TOFU) prevents Man-in-the-Middle attacks even on untrusted networks.

---

## 4. NIST Cybersecurity Framework Alignment

PortDesk's security architecture is aligned with the NIST Cybersecurity Framework (CSF). The following table maps each CSF function to the corresponding control implemented in PortDesk:

| NIST CSF Function | PortDesk Implementation |
| :--- | :--- |
| **Identify** | All assets are local — no external services, no cloud dependencies. Security configuration is stored in a versioned, auditable JSON file (`portdesk_security.json`). |
| **Protect** | IP whitelist with terminal-based approval flow; bcrypt-hashed PIN with escalating lockout (5 attempts → 60s → 180s → 300s); HTTPS/TLS via self-signed certificate; TOFU (Trust On First Use) certificate pinning on the client; CSRF protection on state-changing endpoints; token bucket rate limiting on all WebSocket message types. |
| **Detect** | Real-time event logging to `portdesk_events.log`; multi-IP attack detection triggering automatic lockdown when 5+ unknown IPs attempt connection within 30 seconds; connection attempt counters per IP. |
| **Respond** | Automatic lockdown mode on detected attacks; terminal commands for immediate response (`kick all`, `lockdown off`, `unblock <ip>`); automatic blacklisting after 3 rejected connection attempts from the same IP. |
| **Recover** | Security file backup system (`.bak1`–`.bak3`) with `restore security [n]` terminal command; automatic reload of security configuration on file change without server restart. |

---

## 5. Transparency & Verification

In alignment with our commitment to security, PortDesk is 100% Open Source. Users and security auditors are encouraged to review the source code (`portdesk-server.py` and `portdesk_client.html`) to verify:

* The absence of telemetry, trackers, or external HTTP requests.
* That no data is transmitted to developer-owned infrastructure.
* That the WebSocket and HTTPS implementations use standard, auditable encryption libraries.

---

## 6. User Control

You maintain absolute sovereignty over your data. All configuration files (Macros, Schedules, and Security Logs) are stored locally on your PC and can be audited or deleted at any time without affecting the core software integrity.

**Remote Access is entirely opt-in.** PortDesk does not and cannot expose your server to the internet automatically. You must manually configure your router, firewall, or proxy to enable WAN access.

---

## 7. Network Exposure Disclaimer

When you choose to expose PortDesk to the internet, you assume responsibility for network security. We recommend:

* Using a **strong, unique PIN** (not sequential or repeated digits).
* Enabling **IP Whitelisting** to restrict which devices can request access.
* Using a **reverse proxy** (e.g., Nginx, Traefik) or **VPN** (e.g., WireGuard, Tailscale) instead of direct port forwarding where possible.
* Keeping the server software updated to the latest version.

PortDesk provides the tools to secure your connection, but the security of your network perimeter remains your responsibility.

---

**PortDesk: Privacy by Design. Control by You.**
