"""Portable Python launcher; no PowerShell policy change, autostart or service install."""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime,timezone
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.candidate_server import matching_health

ROOT=Path(__file__).resolve().parents[1]


def observed_service_pid(observed):
    pid=observed.get('pid')
    if type(pid) is not int or pid<=0:raise RuntimeError('健康接口未提供可核对的服务PID')
    return pid


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as response:return json.load(response)
    except (OSError,ValueError):return None


def spawn_windows_server(command,root):
    logs=root/'logs';logs.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    with (logs/f'candidate-{stamp}.out.log').open('ab') as out,(logs/f'candidate-{stamp}.err.log').open('ab') as error:
        process=subprocess.Popen(command,cwd=root,env={**os.environ,'PYTHONUTF8':'1'},stdin=subprocess.DEVNULL,
            stdout=out,stderr=error,close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP|subprocess.CREATE_BREAKAWAY_FROM_JOB)
    return process.pid


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8911)
    parser.add_argument('--mode',choices=['DEMO','READ_ONLY','LIVE'],default='READ_ONLY')
    args=parser.parse_args(argv)
    if args.port in {8798,8904,8910} or not 1024<=args.port<=65535:raise ValueError('禁止原运行端口')
    build=json.loads((ROOT/'candidate-manifest.json').read_text(encoding='utf-8'))['build']
    existing=health(args.port)
    if existing:
        if not matching_health(existing,ROOT,args.mode,build):raise RuntimeError('端口属于其他目录、版本或模式，未停止进程')
        print(f'候选已运行：http://127.0.0.1:{args.port}/command-center');return
    logs=ROOT/'logs';logs.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    command=[sys.executable,'-B','scripts/candidate_server.py','--port',str(args.port),'--mode',args.mode]
    child=None
    if os.name=='nt':launcher_pid=spawn_windows_server([*command,'--background-log'],ROOT)
    else:
        with (logs/f'candidate-{stamp}.out.log').open('ab') as out,(logs/f'candidate-{stamp}.err.log').open('ab') as error:
            child=subprocess.Popen(command,cwd=ROOT,env={**os.environ,'PYTHONUTF8':'1'},stdout=out,stderr=error,start_new_session=True,close_fds=True)
        launcher_pid=child.pid
    for _ in range(30):
        observed=health(args.port)
        if observed:
            if not matching_health(observed,ROOT,args.mode,build):
                raise RuntimeError('启动身份不符；没有停止其他进程，请查日志')
            (ROOT/'runtime/candidate-launch.json').write_text(json.dumps({'pid':observed_service_pid(observed),'launcher_pid':launcher_pid,'port':args.port,'root':str(ROOT),'build':build,'mode':args.mode,'started_at':stamp}),encoding='utf-8')
            print(f'独立候选：http://127.0.0.1:{args.port}/command-center · {build} · {args.mode}');return
        if child and child.poll() is not None:raise RuntimeError(f'启动失败，查看logs/candidate-{stamp}.err.log')
        time.sleep(.5)
    raise RuntimeError('候选未就绪，查看启动日志；没有自动重启')


if __name__=='__main__':main()
