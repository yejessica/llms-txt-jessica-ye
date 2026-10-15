"""Run the real CLI on five sites and retain exact results; no mocked network."""
import json
from pathlib import Path
import subprocess
import sys
import time

SITES=[('example.com','Static','https://example.com'),('docs.python.org','Documentation','https://docs.python.org/3/'),('www.djangoproject.com','Project/product','https://www.djangoproject.com/'),('www.paulgraham.com','Essays','https://www.paulgraham.com/'),('app.diagrams.net','JavaScript application','https://app.diagrams.net/')]

def main():
    out=Path('output');out.mkdir(exist_ok=True); results=[]
    for name,kind,url in SITES:
        target=out/f'{name}-llms.txt'
        command=[sys.executable,'-m','app.cli','generate',url,'--max-pages','30','--max-depth','3','--output',str(target),'--verbose']
        started=time.monotonic()
        try:
            run=subprocess.run(command,capture_output=True,text=True,timeout=600)
            log=run.stdout+'\n'+run.stderr;code=run.returncode
        except subprocess.TimeoutExpired as exc:
            log=str(exc);code=124
        elapsed=round(time.monotonic()-started,3)
        (out/f'{name}.log').write_text(log)
        stats=json.loads(target.with_suffix('.stats.json').read_text()) if code==0 else {}
        row=dict(website=name,type=kind,url=url,exit_code=code,seconds=elapsed,stats=stats)
        results.append(row)
        print(json.dumps(row),flush=True)
    (out/'live-results.json').write_text(json.dumps(results,indent=2)+'\n')

if __name__=='__main__': main()
