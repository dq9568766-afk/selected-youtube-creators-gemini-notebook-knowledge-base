"""Fail closed before publishing repository files. Never prints matching secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
TOP={'.gitignore','.githooks/pre-push','LICENSE','NOTICE.md','README.md','AGENTS.md','SKILL.md','agents/openai.yaml','pyproject.toml',
     'ytg.py','publish_check.py',
     '.agents/skills/youtube-creator-gemini-notebook-knowledge-base/SKILL.md',
     '.agents/skills/youtube-creator-gemini-notebook-knowledge-base/agents/openai.yaml'}
PREFIX=('tests/','docs/','vendor/youtube-summarizer-chrome/','.github/workflows/')
SENSITIVE=re.compile(r'(^|/)(runtime|work|screenshots|\.venv|__pycache__)(/|$)|cookie|token|credential|profile|storage_state|\.env|\.(db|sqlite\d*|log|jsonl)(-|$)',re.I)
SECRETS=[re.compile(x) for x in [r'gh[pousr]_[A-Za-z0-9]{20,}',
          r'github_pat_[A-Za-z0-9_]{20,}',r'AIza[0-9A-Za-z_-]{30,}',
          r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
          r'ya29\.[A-Za-z0-9_-]{20,}']]
PRIVATE_LINK=re.compile(r'https://gemini\.google\.com/(?:app/([0-9a-f]{16})|notebook/([0-9a-f-]{36}))')

def check(paths=None):
    from_index = paths is None
    if paths is None:
        raw=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT)
        paths=[p for p in raw.decode().split('\0') if p]
    failures=[]
    for name in paths:
        if SENSITIVE.search(name) or not (name in TOP or name.startswith(PREFIX)):
            failures.append((name,'unapproved path')); continue
        if from_index:
            data=subprocess.check_output(['git','show',':'+name],cwd=ROOT)
        else:
            path=ROOT/name
            if not path.is_file(): continue
            data=path.read_bytes()
        if b'\x00' in data or len(data)>500_000:
            failures.append((name,'unexpected binary/large file')); continue
        text=data.decode('utf-8-sig')
        if any(p.search(text) for p in SECRETS): failures.append((name,'possible credential'))
        for m in PRIVATE_LINK.finditer(text):
            # Explicitly synthetic fixtures only. Actual saved chat/book ids stay local.
            ident=(m.group(1) or m.group(2)).replace('-','')
            if len(set(ident))!=1:
                failures.append((name,'private Gemini mapping'))
    return failures

if __name__=='__main__':
    findings=check()
    for name,reason in findings: print(name+': '+reason)
    print('Publish check: '+('FAIL' if findings else 'PASS'))
    sys.exit(bool(findings))
