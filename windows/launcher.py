"""Single-instance Windows supervisor. No listeners, password prompts, or extra terminals."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def healthy(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(f'http://127.0.0.1:{port}/global/health')
        password = os.environ.get('OPENCODE_SERVER_PASSWORD')
        if password:
            import base64
            req.add_header('Authorization', 'Basic ' + base64.b64encode(('opencode:' + password).encode()).decode())
        with opener.open(req, timeout=2) as response:
            return json.load(response).get('healthy') is True
    except (OSError, ValueError):
        return False


def stop_process(process):
    if process and process.poll() is None:
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        process.wait(timeout=10)


def supervise(args, state):
    import msvcrt
    lock = (state / 'instance.lock').open('a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0'); lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        if args.receipt: Path(args.receipt).write_text('already')
        print('Already running. Logs:', state)
        return 0
    handler = RotatingFileHandler(state / 'launcher.log', maxBytes=2_000_000, backupCount=3)
    logging.basicConfig(handlers=[handler], level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    server = bridge = None
    stop = state / 'stop'
    ready = state / 'ready'
    stop.unlink(missing_ok=True); ready.unlink(missing_ok=True)
    # Child stdout is rotated on each supervisor start, capped to three previous runs.
    for stem in ('server', 'bridge'):
        for i in range(2, 0, -1):
            previous = state / f'{stem}.{i}.log'
            if previous.exists(): previous.replace(state / f'{stem}.{i+1}.log')
        current = state / f'{stem}.log'
        if current.exists(): current.replace(state / f'{stem}.1.log')
    server_log = (state / 'server.log').open('ab', buffering=0)
    bridge_log = (state / 'bridge.log').open('ab', buffering=0)
    try:
        import serial  # fail early with a useful log before starting any child
        executable = shutil.which('opencode')
        if not executable:
            raise RuntimeError('opencode is not on PATH')
        if not healthy(args.api_port):
            logging.info('Starting OpenCode in %s', args.project)
            command = subprocess.list2cmdline([executable, 'serve', '--hostname', '127.0.0.1', '--port', str(args.api_port)])
            server = subprocess.Popen(command, shell=True, cwd=args.project, stdout=server_log, stderr=server_log,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 45
            while not healthy(args.api_port):
                if server.poll() is not None or time.monotonic() >= deadline or stop.exists():
                    raise RuntimeError('OpenCode failed to become healthy; see server.log')
                time.sleep(.5)
        logging.info('OpenCode healthy; starting Bluetooth bridge on %s', args.com)
        def start_bridge():
            return subprocess.Popen([sys.executable, '-u', str(ROOT / 'bridge' / 'opencode_bt_bridge.py'),
                                     '--com', args.com, '--api-port', str(args.api_port)],
                                    cwd=args.project, stdout=bridge_log, stderr=bridge_log,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        bridge = start_bridge()
        # Detect immediate configuration failures, without waiting indefinitely for a peer.
        time.sleep(2)
        if bridge.poll() is not None:
            raise RuntimeError('Bridge failed to start; see bridge.log (COM port or dependency)')
        ready.write_text(str(os.getpid()))
        if args.receipt: Path(args.receipt).write_text('ready')
        delay = 1
        started = time.monotonic()
        while not stop.exists():
            if server and server.poll() is not None:
                raise RuntimeError('Owned OpenCode server exited; see server.log')
            if bridge.poll() is not None:
                logging.warning('Bridge exited; reopening COM port in %s seconds', delay)
                until = time.monotonic() + delay
                while time.monotonic() < until and not stop.exists(): time.sleep(.25)
                if stop.exists(): break
                bridge = start_bridge()
                delay = min(30, delay * 2)
                started = time.monotonic()
            elif time.monotonic() - started > 60:
                delay = 1
            time.sleep(.5)
        return 0
    except Exception:
        logging.exception('Startup/supervision failed')
        return 1
    finally:
        ready.unlink(missing_ok=True)
        for process in (bridge, server):
            try: stop_process(process)
            except Exception: logging.exception('Could not stop child')
        server_log.close(); bridge_log.close(); lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=ROOT)
    parser.add_argument('--com', default='COM3')
    parser.add_argument('--api-port', type=int, default=4096)
    parser.add_argument('--console', action='store_true', help='Run supervisor in this console for diagnostics')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--stop', action='store_true')
    parser.add_argument('--receipt', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != 'nt': parser.error('This launcher requires Windows')
    if not args.project.is_dir(): parser.error('Project folder does not exist')
    if not 1 <= args.api_port <= 65535: parser.error('Invalid API port')
    if not args.com.upper().startswith('COM') or not args.com[3:].isdigit(): parser.error('Expected COM followed by a number')
    state = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'SerialOpencode' / args.com.upper()
    state.mkdir(parents=True, exist_ok=True)
    if args.stop:
        (state / 'stop').touch()
        print('Stop requested. Logs:', state)
        return 0
    if args.worker or args.console: return supervise(args, state)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    if not pythonw.exists(): parser.error('pythonw.exe not found beside Python; use --console')
    receipt = state / ('startup-' + uuid.uuid4().hex)
    process = subprocess.Popen([str(pythonw), str(Path(__file__).resolve()), '--worker', '--project', str(args.project.resolve()),
                                '--com', args.com, '--api-port', str(args.api_port), '--receipt', str(receipt)], creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        if receipt.exists():
            status = receipt.read_text(); receipt.unlink()
            print(('Already running.' if status == 'already' else 'Running in background.') + ' Logs:', state)
            return 0
        if process.poll() is not None:
            print('Supervisor exited. Check logs:', state)
            return process.returncode
        time.sleep(.25)
    print('Startup timed out. Check logs:', state)
    return 1


if __name__ == '__main__':
    sys.exit(main())
