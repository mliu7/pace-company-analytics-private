"""Stream a verified company snapshot over an authenticated administrative SSH channel.

Run with the server virtualenv as the unprivileged application service account.
Only reads the application database, with read-only transactions also enforced for
pg_dump. Credentials stay on the server. This is not an HTTP endpoint and never
connects to source systems. No code installation or database changes are needed.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

import psycopg2
from psycopg2 import sql
from dotenv import dotenv_values

LOCK_KEY = 815070


def fingerprint(conn):
    with conn.cursor() as cur:
        cur.execute("SET LOCAL TIME ZONE 'UTC'")
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [row[0] for row in cur.fetchall()]
    if any(t.startswith(('notes_', 'private_workspace_')) or t in
           {'analytics_ratingrun', 'analytics_entityrating'} for t in tables):
        raise ValueError('Company database contains private tables')
    result = {}
    for table in tables:
        digest = hashlib.sha256()
        count = 0
        with conn.cursor(name='snapshot_verify') as cur:
            cur.itersize = 10000
            cur.execute(sql.SQL('SELECT md5(row_to_json(t)::text) FROM {} t ORDER BY 1').format(sql.Identifier(table)))
            for row in cur:
                digest.update(row[0].encode())
                count += 1
        result[table] = {'rows': count, 'digest': digest.hexdigest()}
    return result


def export(mode):
    values = dotenv_values('/etc/pca/app.env')
    cfg = {k: values.get('PCA_DB_' + v, default) for k, v, default in
           [('host', 'HOST', '127.0.0.1'), ('port', 'PORT', '5432'),
            ('dbname', 'NAME', ''), ('user', 'USER', ''), ('password', 'PASSWORD', '')]}
    conn = psycopg2.connect(**cfg, connect_timeout=15,
                           options='-c default_transaction_read_only=on -c statement_timeout=300000')
    try:
        # Start the snapshot only after excluding the multi-transaction refresh.
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute('SELECT pg_try_advisory_lock(%s)', [LOCK_KEY])
            if not cur.fetchone()[0]:
                print('Company refresh is running; retry shortly.', file=sys.stderr)
                return 75
        conn.autocommit = False
        conn.set_session(readonly=True, isolation_level='REPEATABLE READ')
        with conn.cursor() as cur:
            cur.execute("SELECT id, finished_at FROM ingestion_ingestionrun WHERE source_system='local' AND status='succeeded' AND finished_at IS NOT NULL ORDER BY finished_at DESC LIMIT 1")
            run = cur.fetchone()
            if not run:
                raise ValueError('No completed company refresh is available')
            meta = {'source_run': run[0], 'source_finished_at': run[1].isoformat(),
                    'exported_at': datetime.now(timezone.utc).isoformat()}
            cur.execute('SELECT pg_export_snapshot()')
            snapshot = cur.fetchone()[0]
        if mode == 'probe':
            print(json.dumps(meta))
            return 0
        with tempfile.TemporaryDirectory(prefix='pca-mirror-') as temp:
            root = Path(temp)
            dump = root / 'company.dump'
            env = {**os.environ, **{'PG' + k: str(cfg[v]) for k, v in
                   [('HOST', 'host'), ('PORT', 'port'), ('DATABASE', 'dbname'),
                    ('USER', 'user'), ('PASSWORD', 'password')]},
                   'PGOPTIONS': '-c default_transaction_read_only=on -c statement_timeout=300000'}
            meta['tables'] = fingerprint(conn)
            subprocess.run(['pg_dump', '-Fc', '--no-owner', '--no-acl',
                            '--snapshot=' + snapshot, '--file=' + str(dump)],
                           env=env, check=True, capture_output=True, timeout=900)
            with dump.open('rb') as stream:
                meta['dump_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
            files = []
            with conn.cursor() as cur:
                for kind, query, setting in [
                    ('bank', 'SELECT storage_name,sha256 FROM finance_bankupload', 'PCA_BANK_STATEMENTS_DIR'),
                    ('reports', 'SELECT storage_name,content_sha256 FROM reports_report', 'PCA_REPORT_FILES_DIR')]:
                    cur.execute(query)
                    for name, digest in cur.fetchall():
                        if not re.fullmatch(r'[a-f0-9]{64}\.(pdf|html)', name) or name.split('.')[0] != digest:
                            raise ValueError('Invalid shared file metadata')
                        path = Path(values.get(setting, '/srv/pca/files/' + kind)) / name
                        with path.open('rb') as stream:
                            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                                raise ValueError('Shared file checksum mismatch')
                        files.append((path, kind + '/' + name))
            meta['files'] = len(files)
            (root / 'manifest.json').write_text(json.dumps(meta))
            # Snapshot/refresh lock is no longer needed while sending immutable files.
            conn.rollback()
            conn.close()
            with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as tar:
                tar.add(root / 'manifest.json', arcname='manifest.json')
                tar.add(dump, arcname='company.dump')
                for path, name in files:
                    tar.add(path, arcname=name)
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        raise SystemExit(export(sys.argv[1]))
    except Exception as exc:
        print('Snapshot export failed (' + type(exc).__name__ + ').', file=sys.stderr)
        raise SystemExit(1)
