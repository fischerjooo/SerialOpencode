# SerialOpencode — OpenCode 1.x over Bluetooth

Unofficial Bluetooth Classic (SPP/RFCOMM) web remote for OpenCode 1.x. Android Chrome connects over Web Serial to a Windows Python bridge on an incoming Bluetooth COM port. **GitHub Pages serves only the app interface; prompts and replies travel over Bluetooth, not over GitHub.**

**Website (after enabling GitHub Pages):** https://fischerjooo.github.io/SerialOpencode/

## Quick start

1. Pair Android with Windows 11 and create an **Incoming Bluetooth COM port** (default COM3). Verify that two-way serial text works.
2. On Windows install Python 3.9+ and `py -3 -m pip install pyserial`.
3. Copy `bridge/opencode_bt_bridge.py` and `windows/Start_OpenCode_Bluetooth.bat` into the same Windows folder. The launcher defaults to `C:\WORK\sandbox\Crypto_TestAppl_QEMU`, OpenCode server `127.0.0.1:4096`, and incoming `COM3`; edit if your machine differs.
4. Run the BAT, enter the same OpenCode API password in the server, bridge and terminal windows. No *additional one-time bridge token* is required.
5. Open **https://fischerjooo.github.io/SerialOpencode/** in Android Chrome 138+, select **Add to Home screen / Install app**, launch it, then tap **Connect BT** and authorize the paired laptop.

You can view and select sessions, read messages, send prompts, and see replies. Tool approvals and full interactive terminal access are not supported.

## Automatic website deployment

`.github/workflows/pages.yml` tests the Python bridge and the web app, then publishes the app on every push to `main`.

**Required one-time setup:** In GitHub go to **Settings → Pages → Build and deployment → Source → GitHub Actions**. This setting cannot be enabled using a workflow's default `GITHUB_TOKEN`. After enabling, rerun the workflow from **Actions** or push a new commit.

The website lives in a GitHub Pages project subpath (`/SerialOpencode/`), so manifest and service-worker references remain relative. `index.html` contains its CSS, JS and UI markup inline; the manifest, service worker and icons are only for PWA installation.

## Security

GitHub Pages is public: don't commit passwords, project source code, or session transcripts. The bridge only accesses the OpenCode HTTP server at `127.0.0.1` and your configured local Bluetooth COM port; it opens no internet listener. Bluetooth pairing is **not** a substitute for application-level access control: removing the one-time bridge token means other devices with serial-service access may submit prompts. Follow your organization's device and data-transfer policies.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests use Python's standard library and optionally Node.js for JavaScript syntax verification.
