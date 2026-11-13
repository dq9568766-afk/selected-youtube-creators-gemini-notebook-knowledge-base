"""Native Messaging host that starts the project-local rename service."""
import json
import os
import struct
import subprocess
import sys
import time
import shutil
import socket
import urllib.request
from pathlib import Path
from queue import Empty, Queue
from threading import Thread


def host_config():
    if not getattr(sys, 'frozen', False):
        return {'project_root': str(Path(__file__).resolve().parent), 'python': sys.executable}
    config = Path(sys.executable).with_name('host-config.json')
    try:
        data = json.loads(config.read_text(encoding='utf-8'))
        value = data['project_root']
        root = Path(value).resolve()
        if (root / 'ytg.py').is_file():
            return {'project_root': str(root), 'python': data.get('python') or 'python'}
    except (OSError, KeyError, TypeError, ValueError):
        pass
    raise RuntimeError('本机组件未找到项目目录')


CONFIG = host_config()
ROOT = Path(CONFIG['project_root'])
PYTHON = CONFIG['python'] if Path(CONFIG['python']).is_file() else (shutil.which('python') or 'python')
child = None


def log_action(action):
    try:
        path=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'DQ-youtube-history-renamer' / 'native-actions.log'
        with path.open('a',encoding='utf-8') as stream:
            stream.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {action!r}\n')
    except OSError:
        pass


def read_message():
    raw = sys.stdin.buffer.read(4)
    if len(raw) != 4:
        return None
    size = struct.unpack('<I', raw)[0]
    if size > 1024 * 1024:
        raise ValueError('message too large')
    data = sys.stdin.buffer.read(size)
    if len(data) != size:
        raise ValueError('truncated message')
    return json.loads(data.decode('utf-8'))


def send(value):
    # Native Messaging is a byte protocol; ASCII JSON avoids Windows code-page
    # conversion in embedded Chromium hosts. JSON.parse restores the Unicode.
    data = json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    sys.stdout.buffer.write(struct.pack('<I', len(data)) + data)
    sys.stdout.buffer.flush()


def stop(run_id=None):
    global child
    if run_id:
        path=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'youtube-gemini-channel-summarizer' / f'history-rename-{run_id}.local.json'
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            request=urllib.request.Request(value['bridge'].rstrip('/')+'/release', data=b'{}', method='POST', headers={
                'Content-Type':'application/json', 'X-YTG-Pairing':value['pairing']})
            with urllib.request.urlopen(request, timeout=3):
                pass
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            pass
    if child and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
    child = None
    return {'ok': True}


def cleanup_run(run_id):
    if not run_id:
        return {'ok': False, 'error': '未选择历史 run'}
    stop(run_id)
    cleaned=cli('cleanup', run_id)
    if cleaned.returncode != 0:
        return {'ok':False,'error':(cleaned.stderr or cleaned.stdout or '残留状态清理失败').strip()[:240]}
    path=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'youtube-gemini-channel-summarizer' / f'history-rename-{run_id}.local.json'
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return {'ok':True,'cleaned':True,'run':run_id}


def cli(*args):
    python = os.environ.get('DQ_YTG_PYTHON') or PYTHON
    return subprocess.run([python, '-X', 'utf8', 'ytg.py', *args], cwd=ROOT, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=20)


def existing_pairing(run_id):
    path=Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'youtube-gemini-channel-summarizer' / f'history-rename-{run_id}.local.json'
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        bridge=value['bridge'].rstrip('/')
        request=urllib.request.Request(bridge+'/heartbeat', data=b'{}', method='POST', headers={
            'Content-Type':'application/json', 'X-YTG-Pairing':value['pairing']})
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status == 200:
                return value
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        pass
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def start(run_id=None, resume_run=False):
    global child
    if child and child.poll() is None:
        return {'ok': False, 'error': '本机服务已经在运行'}
    python = os.environ.get('DQ_YTG_PYTHON') or PYTHON
    if run_id:
        current=existing_pairing(run_id)
        if current:
            return {'ok':True,'pairing':current,'reused':True}
    if run_id:
        # Older panels did not send resume=true. Inspect the selected run so a
        # dormant run is always resumed before serve; never guess another run.
        should_resume=resume_run
        if not should_resume:
            listing=cli('history-rename','candidates')
            try:
                available=json.loads(listing.stdout).get('candidates',[])
                selected=next((item for item in available if item.get('run')==run_id),None)
                if selected is None:
                    return {'ok':False,'error':'所选历史任务不在可用列表中'}
                should_resume=not bool(selected.get('live'))
            except (ValueError, TypeError, json.JSONDecodeError):
                return {'ok':False,'error':'无法读取所选历史任务状态'}
        if should_resume:
            resumed=cli('history-rename','resume',run_id)
            if resumed.returncode != 0:
                return {'ok':False,'error':(resumed.stderr or resumed.stdout or '历史任务恢复失败').strip()[:240]}
    def launch():
        nonlocal python
        global child
        # Never reuse the historical fixed port. A crashed bridge may still
        # answer there with a different pairing key, producing HTTP 403 for a
        # newly generated pairing file.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        args = ['-X', 'utf8', 'ytg.py', 'history-rename', 'serve', run_id,
                '--port', str(port)] if run_id else ['-X', 'utf8', 'ytg.py', 'history-rename', 'serve', '--auto',
                '--port', str(port)]
        child = subprocess.Popen(
            [python, *args],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace',
        )
        lines = Queue()
        Thread(target=lambda: [lines.put(line) for line in child.stdout], daemon=True).start()
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                line = lines.get(timeout=0.25)
            except Empty:
                line = ''
            if line:
                try:
                    info = json.loads(line)
                    if info.get('rename_service') == 'ready':
                        pairing_path = Path(info['pairing_file'])
                        return {'ok': True, 'pairing': json.loads(pairing_path.read_text(encoding='utf-8'))}
                    if info.get('stopped'):
                        return {'ok': False, 'error': info['stopped']}
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            if child.poll() is not None:
                break
        stop()
        return {'ok': False, 'error': '服务未能启动；请检查是否存在唯一有效的历史扩展任务'}

    result = launch()
    # A crashed native-host process can leave only the DB session behind. The
    # selected run is explicit, so repair that run once and retry; never clean
    # another run or silently select a different channel.
    if (not result.get('ok') and run_id and
            'rename bridge is already active' in str(result.get('error', '')).lower()):
        cleaned = cli('cleanup', run_id)
        if cleaned.returncode == 0:
            result = launch()
    return result


try:
    while True:
        message = read_message()
        if message is None:
            break
        action = message.get('action') if isinstance(message, dict) else None
        log_action(action)
        # Keep compatibility with older background workers that forwarded the
        # full native-* message type instead of stripping the prefix.
        if isinstance(action, str) and action.startswith('native-'):
            action = action[7:]
        aliases = {
            'list': 'candidates',
            'read': 'candidates',
            'tasks': 'candidates',
            'available': 'candidates',
            'get-candidates': 'candidates',
            'read-candidates': 'candidates',
            'get': 'candidates',
            'refresh': 'candidates',
            'available-tasks': 'candidates',
            'read-tasks': 'candidates',
            'get-tasks': 'candidates',
        }
        action = aliases.get(action, action)
        if action == 'candidates':
            result = cli('history-rename', 'candidates')
            try:
                value = json.loads(result.stdout)
            except (OSError, ValueError, json.JSONDecodeError):
                value = {'candidates': [], 'error': (result.stderr or result.stdout or '候选任务读取失败').strip()[:240]}
            send(value if result.returncode == 0 else {'ok': False, **value})
        elif action == 'start':
            send(start(message.get('run'), bool(message.get('resume'))))
        elif action == 'stop':
            send(stop(message.get('run')))
        elif action == 'cleanup':
            send(cleanup_run(message.get('run')))
        elif action == 'status':
            send({'ok': bool(child and child.poll() is None)})
        else:
            # Older read-only panels used several action names for refreshing
            # the task list. Treat unknown non-mutating actions as a candidate
            # read; mutating actions remain explicit above.
            result=cli('history-rename', 'candidates')
            try:
                value=json.loads(result.stdout)
            except (OSError, ValueError, json.JSONDecodeError):
                value={'candidates': [], 'error': (result.stderr or result.stdout or '候选任务读取失败').strip()[:240]}
            send(value if result.returncode == 0 else {'ok': False, **value})
finally:
    stop()
