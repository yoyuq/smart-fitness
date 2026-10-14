import argparse
import json
import tempfile
import os
from sqlalchemy.engine import make_url
from pathlib import Path
from alembic import command
from alembic.config import Config
from .database import database_url

def upgrade():
    cfg = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    cfg.set_main_option('script_location', str(Path(__file__).with_name('migrations')))
    cfg.attributes['database_url'] = database_url()
    command.upgrade(cfg, 'head')


def report_destination(args):
    report=Path(args.report).resolve()
    protected=[Path(p).resolve() for p in getattr(args,'source',[])]
    if getattr(args,'output',None):protected.append(Path(args.output).resolve())
    url=make_url(database_url())
    if url.get_backend_name()=='sqlite' and url.database and url.database!=':memory:':protected.append(Path(url.database).resolve())
    if report in protected or report.exists() or Path(args.report).is_symlink():
        raise ValueError('Report must be a new file distinct from all source, target and recovery files')
    report.parent.mkdir(parents=True,exist_ok=True)
    return report


def write_report(path,report):
    with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as stream:
        temp=Path(stream.name)
        stream.write(json.dumps(report,indent=2).encode('utf-8'));stream.flush();os.fsync(stream.fileno())
    try:
        # Same-filesystem exclusive publication; never replace another file.
        os.link(temp,path)
    finally:temp.unlink(missing_ok=True)

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
    report_path=report_destination(args) if hasattr(args,'report') else None
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
        write_report(report_path,report)
        print('Storage report written; row contents and credentials omitted')
    else:
        report=export_recovery(database_url(),args.output)
        write_report(report_path,report)
        print('Verified recovery export written')
