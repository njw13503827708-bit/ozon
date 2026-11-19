"""Explicit independent candidate startup; never discovers or stops other services."""
import argparse
import json
import os
import socket
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def matching_health(health,root,mode,build):
    return bool(health and health.get('service')=='collector-local-ingest' and health.get('status')=='ok'
        and Path(health.get('root','')).resolve()==Path(root).resolve() and health.get('mode')==mode and health.get('build')==build)


def blocked_action(path,method,mode):
    if method=='DELETE' and path.startswith('/api/workbench/products/'):return True
    if method in {'GET','HEAD','OPTIONS'}:return False
    # Scope stays with product preparation and explicitly confirmed publication.
    if '/orders/' in path or '/finance/' in path or '/reply/send' in path or '/inventory/' in path:return True
    if path.startswith('/api/workbench/market-intelligence/search-visibility/') and path.rsplit('/',1)[-1] in {'apply','apply-batch'}:return True
    if mode=='DEMO' and (path.endswith('/run') or any(word in path for word in ('/sync','/validate','/generate','/read-data','/publish','/regenerate','/reconcile','/refresh-ozon-status'))):return True
    if mode!='LIVE' and (path.endswith(('/confirm','/confirm-upload')) or '/stores/' in path and path.endswith('/retry') or path.endswith('/retry-failed') or '/batches/' in path and path.endswith('/run')):return True
    return False


def initialize_candidate(root):
    root=Path(root)
    paths=[root/'runtime/erp.sqlite3',root/'runtime/local-operations.sqlite3',root/'runtime/task-db.sqlite3',
           root/'market-intelligence/market.sqlite',root/'runtime/finance/finance.sqlite3']
    if any(path.exists() for path in paths):raise RuntimeError('已有数据库，禁止使用新库初始化覆盖；请按备份/升级说明单独核对')
    sys.path[:0]=[str(ROOT),str(ROOT/'scripts'),str(ROOT/'market-intelligence'),str(ROOT/'finance-center')]
    from backend.db.erp import initialize
    from backend.repositories.local_operations import LocalOperationsRepository
    from scripts.task_database import initialize as task_initialize
    from market_intelligence import MarketStore
    from finance_center import FinanceCenter
    initialize(root);LocalOperationsRepository(root).initialize();task_initialize(root)
    MarketStore(root/'market-intelligence/market.sqlite').initialize();FinanceCenter(root).initialize()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8911)
    parser.add_argument('--mode',choices=['DEMO','READ_ONLY','LIVE'],default='READ_ONLY')
    parser.add_argument('--initialize-new',action='store_true')
    parser.add_argument('--background-log',action='store_true')
    args=parser.parse_args(argv)
    if args.background_log:
        from datetime import datetime,timezone
        logs=ROOT/'logs';logs.mkdir(exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
        sys.stdout=(logs/f'candidate-{stamp}.out.log').open('a',encoding='utf-8',buffering=1)
        sys.stderr=(logs/f'candidate-{stamp}.err.log').open('a',encoding='utf-8',buffering=1)
    if args.initialize_new:initialize_candidate(ROOT);return
    if args.port in {8798,8904,8910} or not 1024<=args.port<=65535:raise ValueError('候选禁止使用原运行端口')
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',args.port))
        except OSError:raise RuntimeError('端口已被占用，没有停止任何进程；请选择空闲候选端口') from None
    if not (ROOT/'collector/workbench-command-center/dist/index.html').is_file():raise RuntimeError('缺少同源前端构建')
    manifest=ROOT/'candidate-manifest.json'
    build=json.loads(manifest.read_text(encoding='utf-8')).get('build','unpackaged-development') if manifest.is_file() else 'unpackaged-development'
    os.environ.update(ERP_RUNTIME_MODE=args.mode,UPLOAD_MODE='production' if args.mode=='LIVE' else 'dry-run',
                      CAF_MODEL_RUNTIME='backend_api',PYTHONUTF8='1')
    sys.path[:0]=[str(ROOT),str(ROOT/'collector/local-ingest')]
    import app as application
    from fastapi.responses import JSONResponse
    @application.app.middleware('http')
    async def candidate_boundary(request,call_next):
        if blocked_action(request.url.path,request.method,args.mode):
            message='本候选暂不提供本地永久删除；保留原资料与任务，等待完整安全清理验收' if request.method=='DELETE' else f'{args.mode}候选：此平台执行不开放，本地保存可用'
            return JSONResponse(status_code=403,content={'detail':message})
        response=await call_next(request)
        response.headers['X-ERP-Build']=build;response.headers['X-ERP-Mode']=args.mode
        return response
    @application.app.get('/api/workbench/runtime-identity')
    def identity():return {'mode':args.mode,'root':str(ROOT),'build':build,'service':'collector-local-ingest'}
    original_health=application.health
    for route in application.app.routes:
        if getattr(route,'path','')=='/health':
            def health():return {**original_health(),'mode':args.mode,'build':build,'pid':os.getpid()}
            route.endpoint=health;route.dependant.call=health
    from backend.repositories.local_operations import LocalOperationsRepository
    LocalOperationsRepository(ROOT).interrupt_jobs()
    import uvicorn
    uvicorn.run(application.app,host='127.0.0.1',port=args.port,lifespan='off')


if __name__=='__main__':main()
