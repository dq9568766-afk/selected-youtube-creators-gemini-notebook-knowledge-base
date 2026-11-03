import subprocess,sys
exe=r"C:\Users\Administrator\AppData\Local\OpenAI\Codex\bin\22cfb1d302613eae\codex.exe"
p=open(sys.argv[1],encoding="utf-8").read()
r=subprocess.run([exe,"--codex-run-as-apply-patch",p],capture_output=True,text=True,encoding="utf-8",errors="replace")
print(r.returncode); print(r.stdout[-3000:]); print("ERR:",r.stderr[-1500:])
