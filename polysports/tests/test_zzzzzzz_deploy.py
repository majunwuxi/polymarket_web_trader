"""在临时本地 PostgreSQL 中验证备份恢复；所有赛事与订单均为模拟数据。"""
from __future__ import annotations
import os
import shutil
import subprocess
import uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from test_z_auth_integration import pg  # noqa: F401
from test_zzz_trading_integration import env, preview  # noqa: F401


def test_shell_scripts_have_valid_syntax():
    bash=os.environ.get('POLYSPORTS_TEST_BASH') or shutil.which('bash')
    if not bash:pytest.skip('未安装 bash，不能验证脚本语法')
    for script in ('backup.sh','restore-empty.sh','backup-systemd.sh','restore-systemd.sh','certbot-reload-nginx.sh'):
        subprocess.run([bash,'-n','deploy/scripts/'+script],check=True)
    subprocess.run([bash,'-n','deploy/tests/linux-ci.sh'],check=True)


def test_isolated_backup_restore_and_nonempty_refusal(env,tmp_path):
    pg,client,headers,body=env
    bin_dir=os.environ.get('POLYSPORTS_TEST_PG_BIN')
    bash=os.environ.get('POLYSPORTS_TEST_BASH') or shutil.which('bash')
    if not bash or (not bin_dir and not shutil.which('pg_dump')):pytest.skip('未配置 PostgreSQL 客户端与 bash')
    url=make_url(os.environ['POLYSPORTS_TEST_DATABASE_URL'])
    assert url.host in ('127.0.0.1','localhost','::1') and url.database.startswith('polysports_test_')
    result=client.post('/api/v1/single-orders',json=preview(client,headers,body),headers={**headers,'Idempotency-Key':str(uuid.uuid4())})
    assert result.status_code==201
    ident=result.json()['data']['intent']['id']
    dest='polysports_test_restore_'+uuid.uuid4().hex[:12]
    assert dest.startswith('polysports_test_restore_')
    admin=create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as conn:conn.execute(text('CREATE DATABASE '+dest))
    finally:admin.dispose()
    password_file=tmp_path/'pgpass'
    password_file.write_text(f'{url.host}:{url.port}:*:{url.username}:{url.password}\n',encoding='utf-8')
    if os.name!='nt':password_file.chmod(0o600)
    envvars=os.environ.copy()
    envvars.update(PGHOST=url.host,PGPORT=str(url.port),PGUSER=url.username,PGDATABASE=url.database,PGPASSFILE=str(password_file))
    if bin_dir:envvars['PATH']=bin_dir+os.pathsep+envvars.get('PATH','')
    backup=subprocess.run([bash,'deploy/scripts/backup.sh',str(tmp_path)],env=envvars,text=True,encoding="utf-8",errors="replace",capture_output=True)
    assert backup.returncode==0,backup.stderr
    files=list(tmp_path.glob('*.dump'));assert len(files)==1
    # 已有生产记录的模拟来源库不得被恢复覆盖。
    refused=subprocess.run([bash,'deploy/scripts/restore-empty.sh',str(files[0])],env=envvars,text=True,encoding="utf-8",errors="replace",capture_output=True)
    assert refused.returncode!=0
    envvars['PGDATABASE']=dest
    restored=subprocess.run([bash,'deploy/scripts/restore-empty.sh',str(files[0])],env=envvars,text=True,encoding="utf-8",errors="replace",capture_output=True)
    assert restored.returncode==0,restored.stderr
    target=create_engine(url.set(database=dest))
    try:
        with pg.connect() as source,target.connect() as copy:
            for table in ('games','trade_intents','jobs','alembic_version'):
                assert source.execute(text('SELECT count(*) FROM '+table)).scalar_one()==copy.execute(text('SELECT count(*) FROM '+table)).scalar_one()
            assert copy.execute(text('SELECT count(*) FROM trade_intents WHERE id=:id'),{'id':uuid.UUID(ident)}).scalar_one()==1
            assert source.execute(text('SELECT version_num FROM alembic_version')).scalar_one()==copy.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    finally:target.dispose()
