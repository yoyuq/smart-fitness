import argparse
import json
import tempfile
from pathlib import Path
from alembic import command
from alembic.config import Config
from .database import database_url

def upgrade():
    cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    cfg.set_main_option('script_location', str(Path(__file__).with_name('migrations')))
    cfg.attributes['database_url'] = database_url()
    command.upgrade(cfg, 'head')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('upgrade')
    snap=sub.add_parser('snapshot');snap.add_argument('--source',required=True);snap.add_argument('--output',required=True)
    for name in ('classify','migrate'):
        p=sub.add_parser(name);p.add_argument('--source',action='append',required=True);p.add_argument('--report',required=True)
        if name=='migrate':
            p.add_argument('--writers-stopped',action='store_true',required=True)
            p.add_argument('--allow-legacy-orphans',action='store_true')
    export=sub.add_parser('export-recovery');export.add_argument('--output',required=True);export.add_argument('--report',required=True);export.add_argument('--writers-stopped',action='store_true',required=True)
    args=parser.parse_args()
    from .transfer import snapshot, classify, import_sqlite, export_recovery
    if args.command=='upgrade':upgrade()
    elif args.command=='snapshot':print(json.dumps(snapshot(args.source,args.output)))
    elif args.command in ('classify','migrate'):
        # Backup includes WAL. Independent source files require paused writers
        # for a globally consistent production cutover; classify can be online.
        with tempfile.TemporaryDirectory(prefix='fitness-snapshot-') as tmp:
            sources=[]
            for index,source in enumerate(args.source):
                output=Path(tmp)/f'source-{index}.db';snapshot(source,output);sources.append(output)
            if args.command=='classify':_,report=classify(sources)
            else:report=import_sqlite(sources,database_url(),allow_legacy_orphans=args.allow_legacy_orphans)
        Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Storage report written; row contents and credentials omitted')
    else:
        report=export_recovery(database_url(),args.output)
        Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('Verified recovery export written')
