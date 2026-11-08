# SerialOpencode — OpenCode over Bluetooth

A standalone, installable web remote for a local OpenCode 1.x server. GitHub Pages delivers the interface; session data travels over paired Bluetooth Classic SPP / Web Serial, not through GitHub or a relay.

## Start on Windows

1. Install OpenCode and Python 3.9+; run `py -3 -m pip install -r requirements.txt`.
2. Pair the devices and configure a Windows **incoming Bluetooth COM port**.
3. Keep the repository folder structure intact. Run:

   ```bat
   windows\Start_OpenCode_Bluetooth.bat --project "C:\path\to\your\project" --com COM3
   ```

4. Open https://fischerjooo.github.io/SerialOpencode/ in a browser supporting Bluetooth Web Serial. Tap **Connect BT** and choose the paired device. Browser/platform support varies; the app reports missing Web Serial. Install via the browser's home-screen menu if available.

The launcher starts the server on `127.0.0.1:4096` and the bridge hidden. The initial BAT console closes after startup. It does **not** open an interactive OpenCode terminal. To attach manually, run `opencode attach http://127.0.0.1:4096`.

Defaults: project = repository root, COM3, API port 4096. Override with `--project`, `--com`, `--api-port`. A lock prevents duplicate supervisors for the same COM port. An already healthy OpenCode server is reused; its existing project context remains in effect. Use another API port for another project.

Stop with `windows\Stop_OpenCode_Bluetooth.bat --com COM3`. Only children started by this supervisor are stopped; a reused server is left running. `--console` runs the supervisor visibly for diagnosis. Logs are in `%LOCALAPPDATA%\SerialOpencode\COM3\` (launcher, server, bridge). Launcher logs rotate by size; child logs rotate on launch. The supervisor retries a disconnected COM port with bounded backoff. Startup has a health-check deadline rather than an endless wait.

**Upgrade:** replace the whole checkout, stop the old launcher/bridge, then start this version. `remote.py` must stay beside `opencode_bt_bridge.py`; copying only the old single bridge file is no longer sufficient. Reload the web app for the v3 cache. Do not run an old manually launched bridge alongside the supervisor.

## Features

- Recent 100 sessions, local search, create/select, rename and confirmed deletion.
- Acknowledged multiline prompts, Unicode, literal slash-prefixed prompt text, default or explicit agent/model.
- Stop generation, live session status, incremental reply updates, copy messages and export the loaded transcript.
- Pending tool permissions (allow once / allow for session / reject) and question replies, including multiple choice and custom answers.
- Draft retention while switching sessions during the current page lifetime; failed or unacknowledged prompts remain in the editor.
- Serialized writes, bounded input, partial-write handling, per-session event filtering, and six bounded reconnect attempts after an unexpected disconnect. Prompts are never automatically replayed.
- Accessible control labels, mobile drawer, reduced-motion support, offline app shell.

No additional authentication is added. Paired devices are trusted. The bridge opens no network listener and accesses OpenCode only on loopback. Optional `OPENCODE_SERVER_PASSWORD` is supported solely for an already password-protected server; no password is required or requested by default. OpenCode's tool approvals remain explicit remote-control actions, not a second login.

## Development

The deployed `index.html` remains self-contained with no runtime dependencies. Maintain readable sources in `web/app.js`, `web/styles.css`, and `web/index.template.html`; generate the artifact rather than editing it directly:

```sh
npm ci
npm run format
python scripts/build_web.py
python -m unittest discover -s tests -v
npx playwright install chromium
npm test
```

`CHROMIUM_PATH` can select an existing browser. CI checks the generated artifact, Python/protocol tests, and a real Chromium UI test with a simulated serial transport. Pages deploys only after tests on `main`; pull requests test without deployment. Enable **Settings → Pages → GitHub Actions** for publishing.

## Compatibility and release validation

The implemented API contract targets OpenCode 1.x, checked against the 1.2.0 permission/question routes and documented session APIs. A missing permission/question endpoint is reported and disabled; catalog failures retain the default agent/model controls. This is not an OpenCode 2.x API client.

Polling is adaptive: 0.5 seconds while busy, at least 3 seconds while idle, with error backoff. It is not SSE streaming. HTTP operations have an 8-second timeout, during which the single bridge loop cannot process another command. Replies are capped at 12,000 characters per message, history at the latest 50 fetched / 20 displayed on request, and rendered chat at 200 rows. Export includes loaded text only. Drafts are in memory and do not survive closing the page. No full terminal, attachments, arbitrary filesystem UI, or full-history pagination is included.

A prompt timeout/disconnect has an **unknown outcome**: inspect history before manually sending again. Request IDs suppress repeated delivery only within the bridge process's latest 128 requests, not across restarts. Protocol details: [docs/protocol.md](docs/protocol.md).

Before a production rollout, validate on the actual Windows/Android pair: hidden startup and duplicate launch, COM removal/reconnect, server restart, stop ownership, long Unicode replies, tool approvals, question replies, install/offline/reload and an interrupted prompt. Automated mock transport tests cannot certify Bluetooth driver behavior or Windows console visibility.
