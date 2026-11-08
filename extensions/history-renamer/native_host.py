"""Native Messaging host that starts the project-local rename service."""
import json
import os
import struct
import subprocess
import sys
import time
from queue import Empty, Queue
from threading import Thread
from pathlib import Path

_host_file = Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve()
ROOT = _host_file.parents[3] if getattr(sys, 'frozen', False) else _host_file.parents[2]
child = None

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
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    sys.stdout.buffer.write(struct.pack('<I', len(data)) + data)
    sys.stdout.buffer.flush()

def start():
    global child
    if child and child.poll() is None:
        return {'ok': False, 'error': '本机服务已经在运行'}
    python = os.environ.get('DQ_YTG_PYTHON') or sys.executable
    if Path(python).suffix.lower() not in {'.exe', '.com'}:
        python = 'python'
    child = subprocess.Popen(
        [python, 'ytg.py', 'history-rename', 'serve', '--auto'],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding='utf-8', errors='replace',
    )
    lines = Queue()
    Thread(target=lambda: [lines.put(line) for line in child.stdout], daemon=True).start()
    deadline = time.time() + 15
    while time.time() < deadline:
        try: line = lines.get(timeout=0.25)
        except Empty: line = ''
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

def stop():
    global child
    if child and child.poll() is None:
        child.terminate()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill()
    child = None
    return {'ok': True}

try:
    while True:
        message = read_message()
        if message is None:
            break
        action = message.get('action') if isinstance(message, dict) else None
        if action == 'start': send(start())
        elif action == 'stop': send(stop())
        elif action == 'status': send({'ok': bool(child and child.poll() is None)})
        else: send({'ok': False, 'error': '未知本机操作'})
finally:
    stop()
