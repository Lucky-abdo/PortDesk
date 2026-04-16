<div align="center">

# 🎮 PortDesk

**Control your PC from any device browser. No app needed.**

[![Python](https://img.shields.io/badge/Python-3.8+-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)]()

</div>

---

## ⚠️ Official Source

**The only official and verified release of PortDesk is hosted at:**  
👉 `https://github.com/Lucky_abdo/PortDesk`

Copies or forks obtained from third-party websites, messaging groups, or unofficial links may have been modified. We cannot guarantee that such versions retain the same privacy standards, security model, or integrity as the original. Please exercise caution and always verify the source before running any code on your machine.

---

## What is PortDesk?

PortDesk turns any device into a full PC controller over your local network (WiFi or USB).  
No installation needed — just open a URL in any browser.  
Everything runs **locally on your machine**. Nothing is sent to the internet. Ever.

---

### Lazy to read ?
give this prompt to ai
```bash
Read the following PortDesk documentation and give the user a clear, concise summary covering only: what PortDesk is, how to install it, how to run it, and the most important notes (HTTPS for mic/gyro, FFmpeg for H264 streaming, dxcam for best Windows performance, PIN security, whitelist system). Skip detailed platform troubleshooting and feature descriptions. Be brief.

--- PASTE DOCUMENTATION BELOW ---
```

## Quick Start

### 1. Install dependencies

```bash
pip install fastapi uvicorn pyautogui opencv-python mss psutil pyperclip sounddevice numpy Pillow python-multipart
```

**Optional (for better performance):**
```bash
pip install dxcam          # Windows only — faster screen capture via DirectX
pip install PyTurboJPEG    # faster JPEG encoding
pip install aiortc         # WebRTC support
pip install cryptography   # for HTTPS/SSL
```

**Optional (for H264 hardware streaming):**
```bash
# Windows
winget install ffmpeg
# Linux
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```
> Without FFmpeg, streaming falls back to JPEG mode automatically.

### 2. Run the server

```bash
python portdesk-server.py
```

## **Recomended run it by start file

You'll see something like:
```
══════════════════════════════════════════════════
  🎮 PortDesk v1.0
══════════════════════════════════════════════════
  [USB]  adb reverse tcp:5000 tcp:5000 → http://localhost:5000
  [WiFi] http://192.168.1.x:5000
══════════════════════════════════════════════════
```

### 3. Open on any device

- **WiFi:** Open `http://192.168.1.x:5000` in any browser on any device on the same network
- **USB (Android):** Run `adb reverse tcp:5000 tcp:5000` first, then open `http://localhost:5000`

---

## Connection Modes

### Hotspot Mode (no router needed)
You can use PortDesk without any WiFi router by creating a hotspot directly from your PC.

**Windows:**
1. Go to **Settings → Network → Mobile Hotspot** and turn it on
2. Connect your phone to your PC's hotspot
3. Run `portdesk-server.py` — the IP shown in the terminal will be your hotspot IP
4. Open that IP in your phone's browser

**Android tethering (reverse):**
1. Connect your phone via USB
2. Enable USB tethering on your phone
3. Run `adb reverse tcp:5000 tcp:5000`
4. Open `http://localhost:5000` on your phone

> Hotspot mode works with zero internet — the connection stays local between your PC and your device.

### WiFi Mode
- Connect your device and PC to the same WiFi network
- Open the IP shown in the terminal on any browser
- Works from phones, tablets, laptops, other PCs — anything with a browser
**make sure that you are on the main network not guest or data network

### USB Mode — Android
Requires [ADB (Android Debug Bridge)](https://developer.android.com/tools/adb) installed on PC.

```bash
# Enable USB Debugging on your Android device first, then:
adb reverse tcp:5000 tcp:5000
```

Then open `http://localhost:5000` on your device.

> For non-Android devices (iPhone, iPad, laptop) just use WiFi mode — USB mode is Android-specific.  

---

## 🌐 Remote Access (Internet)

PortDesk operates in **two layers** automatically:

| Layer | Mechanism | When |
|---|---|---|
| 1 | Direct WebSocket (LAN/USB) | Same network — always tried first |
| 2 | WebRTC + STUN (P2P) | Different networks — auto-fallback |

Layer 2 uses free Google STUN servers and requires no installation. It will work if neither device is behind double-NAT (CGNAT). If it fails, see the options below.

---

### Going beyond STUN — using a VPN tunnel

If STUN fails (common with mobile data or ISP CGNAT), the recommended approach is a VPN overlay tool. These create a virtual LAN between your devices, making PortDesk work exactly as if both were on the same WiFi — no code changes needed on either side.

**Recommended: NetBird** (open-source, self-hostable, stronger security model)
- Download: https://netbird.io
- Install on both PC and phone, sign in with the same account
- Use the NetBird IP shown in the dashboard instead of your LAN IP

**Alternative: Tailscale** (easier setup, same concept)
- Download: https://tailscale.com
- Install on both devices, sign in
- Use the Tailscale IP (100.x.x.x) instead of your LAN IP

> **Note on MTU:** Both tools use a slightly reduced MTU (~1280–1400 bytes) to accommodate encryption headers. PortDesk's upload and streaming code handles this transparently via chunked I/O — no manual configuration needed.

> **Security note:** PortDesk's whitelist system uses the tunnel IP. Add it once via Settings → Add this device to whitelist.

---

## HTTPS Setup (required for microphone)

The mobile microphone feature requires a secure connection (HTTPS).

```bash
python gen_cert.py     # generates cert.pem and key.pem
python portdesk-server.py   # auto-detects the certificates and starts HTTPS
```

Then use `https://` instead of `http://` when opening on your device.  
Accept the self-signed certificate warning in the browser.

---

## Features

### 🖱️ Touch Tab — Touchpad & Mouse
- Move finger = move mouse
- Tap = left click
- Long press = right click
- Two fingers = scroll
- Scroll buttons on the side

### 🌀 Gyroscope Mouse
Press the **🌀 Gyro** button to use your device's physical tilt (mobile/tablet) as mouse movement.
- **Calibrate (🎯):** Sets the current phone angle as "center"
- **Sensitivity:** Adjustable in Settings
- **Dead Zone:** Minimum tilt before movement registers

### 🧠 AI Gyro Learning
Press **🧠 AI** to enable the AI sensitivity calibration system.  
It watches how you move and automatically adjusts gyro sensitivity and dead zone to match your natural hand movement style.  
The more you use it, the more accurate it gets.  
Your learned profile is saved in the browser and survives page refreshes.  
Reset it anytime from Settings → AI Gesture Reset.

### ⌨️ Shortcuts Tab
Pre-built keyboard shortcuts organized by category:
- **System:** Copy, Paste, Undo, Redo, Select All, Save, Cut, Search
- **Windows:** Alt+Tab, Win+D (Desktop), Win+L (Lock), Alt+F4, Win+E, Task Manager, Screenshot, Task View
- **Media:** Play/Pause, Next, Previous, Mute, Volume Up/Down

### 📽️ Presentation Tab
Dedicated controls for PowerPoint / Google Slides:
- Start / End slideshow
- Next / Previous slide
- Arrow keys, Black screen, White screen

### 🕹️ Gaming Tab
Customizable gamepad layout with two controller types: **PS** and **Xbox**.

**Profiles:** Create multiple profiles for different games. Long-press a profile to delete it.

**Full Mode 🎮:** Hides all UI and shows only the controller canvas in fullscreen. Tap "Exit Gaming Mode" to return.

**Editor (✏️):** Customize button layout:
- **Hold & drag** a button from the Palette (top bar) onto the canvas
- **Tap** a placed button to select it
- **Long press** a placed button to assign a key/shortcut to it
- **Drag** the resize handle (bottom-right) to resize
- **Tap ✕** to remove a button

**Camera Wheel:** A special circular control in the palette (⊙) that renders as a steering wheel. Use it for camera control in 3D games — drag your finger to push the wheel in any direction and it continuously moves the mouse.

**Stats (📊):** Shows CPU usage, temperature, and RAM usage in real-time while gaming.

### 🖥️ Screen Tab
Mirror your PC screen to your device.
- Adjust resolution, quality, FPS
- Multi-monitor support
- Cursor color customization
- Touchpad and keyboard available while viewing

**Streaming modes:**
- **H264** (default when FFmpeg is installed): hardware-encoded, low bandwidth, smooth at high FPS
- **JPEG** (fallback): works without FFmpeg, higher bandwidth

> On Windows, install `dxcam` for best capture performance (`pip install dxcam`). Install FFmpeg for H264 hardware encoding (`winget install ffmpeg`).

### 📁 Explorer Tab
Browse, manage, and transfer files between your device and PC.

- **Navigate:** Tap folders to open, tap files to download
- **Breadcrumb:** Shows current path, tap any part to jump back
- **Sort:** By name, date, size, or type
- **Select:** Tap items to select, then Download / Delete
- **Upload:** Tap the Upload button to send files from your device to the PC
- **New folder / New file:** Buttons in the toolbar
- **Long press or right-click** any item for the context menu:
  - Copy, Cut, Paste
  - Rename
  - Create Shortcut
  - Properties
  - Delete

### ⚡ Macros Tab
Record and replay sequences of actions.

1. Tap **⏺ Record**
2. Add steps: Click, Key, Type text, or Wait
3. Give it a name and tap **💾 Save**
4. Tap **▶ Run** to execute it anytime

Macros are saved on the PC in `portdesk_macros.json`.

### 📊 Tasks Tab
View running processes, sorted by CPU usage. Tap ⛔ to terminate any process.  
Enable **Auto** for live refresh every 3 seconds.

### ⏰ Sched Tab
Schedule macros to run at specific times.
1. Pick a time
2. Name the task
3. Select a macro
4. Tap **+ Add**

Enable/disable individual tasks with the toggle. The server checks every 10 seconds.

### 📝 Log Tab
Two views:
- **Server:** Events logged by the server (connections, pin attempts, audio start/stop, etc.)
- **Client:** Real-time JavaScript log from your device — useful for debugging

### ⚙️ Settings
- Touch sensitivity, gyro sensitivity, dead zone, scroll speed
- Haptic feedback, sound feedback
- Auto-sleep timer
- PIN lock
- Backup & restore settings (JSON export/import)
- Whitelist management
- AI reset
- Light/Dark theme
- Language (Arabic / English)
- Remote Audio (requires VB-Audio on Windows)
- Mobile Microphone

---

##  Security & Whitelist

PortDesk is designed for local network use only. It has a built-in IP whitelist system.

**How it works:**
- When a new device tries to connect over WiFi, the server prints a prompt in the terminal asking you to allow or deny it
- Type `y` to add the device to the whitelist, `n` to reject
- The whitelist is saved in `portdesk_security.json`
- Whitelisted devices connect automatically in the future

**Manage the whitelist from the phone:**
- Settings → Add this device to whitelist (adds current phone)
- Settings → Clear whitelist (reset — all devices must re-request access)

**PIN Lock:**
- Settings → Set PIN — set a 4-digit PIN
- Anyone opening the URL must enter it before accessing the controller
- PIN is stored as a salted SHA-256 hash in the browser, never sent to the server in plain text
- After 5 wrong attempts, the server locks the IP for 60 seconds

---

## 🔊 Remote Audio & Microphone

### Remote Audio (PC → Phone)
Streams your PC's audio to your device in real-time.

**Windows:** Requires [VB-Audio Virtual Cable](https://vb-audio.com/Cable/).  
Set your app's audio output to "CABLE Input". PortDesk reads from "CABLE Output".

**Linux:** Uses PulseAudio — no extra software needed.

### Microphone (Device → PC)
Uses your device's microphone as a virtual mic on the PC.

**Windows:** Requires VB-Audio Virtual Cable. PortDesk outputs to "CABLE Input".  
**Requires HTTPS** (see HTTPS setup above).

---

## Platform Notes

| Feature | Windows | Linux | macOS |
|---|---|---|---|
| Mouse & Keyboard | ✅ | ✅ | ✅ |
| Screen Mirroring | ✅ | ✅ | ✅ |
| CPU Temperature | ✅ CoreTemp | ✅ via psutil | ✅ powermetrics |
| Win/Meta shortcuts | ✅ | ✅ | ✅ |
| Remote Audio | ✅ VB-Audio | ✅ PulseAudio | ⚠️ Limited |
| Mobile Mic | ✅ VB-Audio | ✅ | ⚠️ Limited |
| File Shortcuts (.lnk) | ✅ | .desktop | symlink |

---

## Known Limitations

- Screen mirroring FPS depends on your network speed and PC performance
- H264 streaming requires FFmpeg installed in PATH — falls back to JPEG automatically without it
- Mobile microphone requires HTTPS (generate a certificate first)
- Remote audio on macOS has limited support
- Gyroscope on iOS requires user permission (browser will ask on first use)
- Win key shortcuts (Win+D, Win+L, etc.) may not work if the server terminal window has focus — minimize it or use the `.bat` launcher

---

## Files

| File | Description |
|---|---|
| `portdesk-server.py` | The FastAPI server — run this on your PC |
| `portdesk_client.html` | The phone UI — served automatically by the server |
| `gen_cert.py` | Generates self-signed SSL certificate for HTTPS |
| `fixer.py` | Diagnostics and auto-repair tool |
| `requirements.txt` | Python dependencies |
| `portdesk_security.json` | Whitelist (auto-created, not in repo) |
| `portdesk_macros.json` | Saved macros (auto-created) |
| `portdesk_scheduled.json` | Scheduled tasks (auto-created) |
| `portdesk_events.log` | Server event log (auto-created) |

---

## License

MIT License — see [LICENSE](LICENSE)

---

## A note on development

PortDesk was built with the assistance of AI tools. This is mentioned in the spirit of transparency — the architecture, decisions, and direction were by me, but AI was used throughout the development process for code generation, debugging, and documentation.
