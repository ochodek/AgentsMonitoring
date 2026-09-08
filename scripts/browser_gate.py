"""Validate the built wheel against harmless processes on a private tmux socket."""
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, check=True, timeout=120, **kwargs)


def main():
    real_tmux = shutil.which('tmux')
    assert real_tmux, 'tmux is required'
    with tempfile.TemporaryDirectory(prefix='agentsmon-gate-') as directory:
        root = Path(directory)
        wheel_dir = root / 'dist'
        run(sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', str(wheel_dir), str(ROOT))
        package = root / 'package'
        run(sys.executable, '-m', 'pip', 'install', '--no-deps', '--target', str(package), str(next(wheel_dir.glob('*.whl'))))
        bins = root / 'bin'
        bins.mkdir()
        tmux_socket = 'agentsmon-gate-' + str(os.getpid())
        wrapper = bins / 'tmux'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(real_tmux) + ' -L ' + tmux_socket + ' -f /dev/null "$@"\n')
        wrapper.chmod(0o700)
        source = root / 'fixture.c'
        source.write_text('#include <fcntl.h>\n#include <unistd.h>\nint main(int argc, char **argv) { if (argc != 2 || open(argv[1], O_RDONLY) < 0) return 2; for (;;) pause(); }\n')
        run('cc', str(source), '-o', str(bins / 'codex'))
        env = os.environ.copy()
        env.update(HOME=str(root), PATH=str(bins) + os.pathsep + env['PATH'],
                   PYTHONPATH=str(package), AGENTSMON_CONFIG=str(root / 'config.json'),
                   AGENTSMON_STATE=str(root / 'state'))
        # No user shell startup files or real AI processes are involved.
        def tm(*args):
            return run(str(wrapper), *args, env=env, capture_output=True, text=True)

        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base_url = f'http://127.0.0.1:{port}'
        config = {'dashboard': {'host': '127.0.0.1', 'port': port, 'poll_seconds': 1},
                  'modules_dir': str(root / 'no-modules'), 'services': [],
                  'keepalive': {'enabled': False},
                  'pinned_daemons': [{'name': 'Hermes', 'process': 'agentsmon-nonexistent-gate-daemon'}]}
        (root / 'config.json').write_text(json.dumps(config))
        hermes = root / '.hermes'
        hermes.mkdir()
        (hermes / 'config.yaml').write_text('model:\n  default: gpt-6-astra\n')
        ids = ['11111111-1111-1111-1111-111111111111', '22222222-2222-2222-2222-222222222222']
        paths = []
        server = None
        try:
            for number, model in enumerate(('gpt-5.5', 'gpt-5.6-sol')):
                path = root / f'rollout-{ids[number]}.jsonl'
                path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': ids[number], 'cwd': str(root), 'source': 'cli'}}) + '\n' +
                                json.dumps({'type': 'turn_context', 'payload': {'model': model}}) + '\n')
                paths.append(path)
                tm('new-session', '-d', '-s', f'Gate{number}', '-c', str(root),
                   shlex.join([str(bins / 'codex'), str(path)]))
            with (root / 'dashboard.log').open('w') as output:
                server = subprocess.Popen([sys.executable, '-m', 'agentsmon', 'dashboard'], cwd=root, env=env,
                                          stdin=subprocess.DEVNULL, stdout=output, stderr=output)
            for _ in range(50):
                try:
                    urllib.request.urlopen(base_url, timeout=1).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise AssertionError((root / 'dashboard.log').read_text())
            errors, failures, assets = [], [], set()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)

                def response_received(response):
                    parsed = urlsplit(response.url)
                    if parsed.netloc != urlsplit(base_url).netloc:
                        return
                    if response.status >= 500 or (parsed.path.startswith('/static/') and response.status >= 400):
                        failures.append((parsed.path, response.status))
                    if parsed.path.startswith('/static/'):
                        assets.add(parsed.path)

                page.on('response', response_received)
                page.on('requestfailed', lambda request: failures.append(request.url)
                        if request.url.startswith(base_url + '/static/') else None)
                response = page.goto(base_url, wait_until='networkidle')
                assert response and response.ok
                expect(page).to_have_title('Agents Monitoring')
                expect(page.get_by_text('Persistent Agents', exact=True)).to_be_visible()
                first = page.locator('tr').filter(has=page.get_by_text('Gate0', exact=True))
                second = page.locator('tr').filter(has=page.get_by_text('Gate1', exact=True))
                daemon = page.locator('tr').filter(has=page.get_by_text('Hermes', exact=True))
                expect(first).to_contain_text('GPT-5.5')
                expect(second).to_contain_text('GPT-5.6 Sol')
                expect(daemon).to_contain_text('GPT-6 Astra')
                # Same process, same directory, a later turn: only this row changes.
                with paths[0].open('a') as output:
                    output.write('{}\n' * 100 + json.dumps({'type': 'turn_context', 'payload': {'model': 'gpt-6-astra'}}) + '\n')
                expect(first).to_contain_text('GPT-6 Astra', timeout=15000)
                expect(first).to_contain_text(ids[0])
                expect(second).to_contain_text('GPT-5.6 Sol')
                expect(second).to_contain_text(ids[1])
                (hermes / 'config.yaml').write_text('model:\n  default: gpt-5.6-sol\n')
                expect(daemon).to_contain_text('GPT-5.6 Sol', timeout=15000)
                assert '/static/tailwind.js' in assets, 'primary static asset missing'
                assert not errors, errors
                assert not failures, failures
                browser.close()
            print('PASS: built wheel, primary page, current per-process models, runtime switches, assets and browser errors')
        finally:
            if server:
                server.terminate()
                server.wait(timeout=10)
            subprocess.run([str(wrapper), 'kill-server'], env=env, capture_output=True, timeout=10)


if __name__ == '__main__':
    main()
