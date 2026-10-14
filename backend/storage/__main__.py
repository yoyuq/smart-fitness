import argparse
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
    parser.add_argument('command', choices=['upgrade'])
    parser.parse_args()
    upgrade()
