#!/usr/bin/env python3
"""Restore a private C4W bundle into disposable PostgreSQL and verify all rows."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time


def run(*args, **kwargs):
    return subprocess.run(*args, check=True, **kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('backup',type=Path)
    parser.add_argument('--keep',action='store_true',help='Keep the isolated server for browser QA')
    args=parser.parse_args();root=args.backup.resolve();os.umask(0o077)
    for line in (root/'SHA256SUMS').read_text().splitlines():
        digest,name=line.split('  ',1)
        h=hashlib.sha256()
        with (root/name).open('rb') as stream:
            for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
        assert h.hexdigest()==digest,name
    name='c4w-restore-'+secrets.token_hex(4)
    # Bound only to loopback; this role is confined to the throw-away container.
    password=secrets.token_urlsafe(24)
    envfile=root/'private'/'restore.env'
    envfile.write_text('POSTGRES_PASSWORD='+password+'\nPOSTGRES_DB=c4w_restore\n')
    run(['docker','run','-d','--rm','--name',name,'--env-file',str(envfile),'-p','127.0.0.1::5432','postgres:17'],stdout=subprocess.DEVNULL)
    try:
        for _ in range(40):
            # The initialization server accepts socket connections before the
            # requested database exists. Wait for the final TCP server and DB.
            if subprocess.run(['docker','exec',name,'psql','-X','-h','127.0.0.1','-U','postgres','-d','c4w_restore','-Atc','SELECT 1'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:break
            time.sleep(.5)
        else:raise RuntimeError('PostgreSQL did not start')
        psql=['docker','exec','-i',name,'psql','-X','-v','ON_ERROR_STOP=1','-U','postgres','-d','c4w_restore']
        with (root/'restore.log').open('wb') as log:
            for source in ('schema.sql','data.sql'):
                with (root/'database'/source).open('rb') as data:
                    run(psql,stdin=data,stdout=log,stderr=log)
        counts=json.loads((root/'database'/'counts.json').read_text())
        sql_data=(root/'database'/'data.sql').read_text()
        copied={}
        for match in re.finditer(r'^COPY (.+?) FROM stdin;\n(.*?)^\\\.\n',sql_data,re.M|re.S):
            table=match.group(1).split(' (',1)[0]
            copied[table]=(match.group(1),match.group(2))
        verified={}
        for table,expected in counts.items():
            target,body=copied['"'+table+'"']
            output=subprocess.check_output(psql+['-Atc','COPY '+target+' TO STDOUT']).decode()
            assert sorted(output.splitlines())==sorted(body.splitlines()),'Row mismatch: '+table
            actual=len(output.splitlines());assert actual==expected,(table,actual,expected)
            verified[table]=actual
        media=json.loads((root/'media.json').read_text())
        for item in media:
            data=(root/item['path']).read_bytes()
            assert len(data)==item['size'] and hashlib.sha256(data).hexdigest()==item['sha256']
        report={'result':'passed','tables':verified,'rows':sum(verified.values()),'media_objects':len(media),
                'media_bytes':sum(x['size'] for x in media),'postgres_image':'postgres:17',
                'validation':'Every restored COPY row matches the source snapshot; all media sizes and SHA-256 match.'}
        if args.keep:
            report['isolated_container']=name
            report['isolated_port']=subprocess.check_output(['docker','port',name,'5432']).decode().strip()
        (root/'verification.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
    finally:
        if not args.keep:
            subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,check=True)
            envfile.unlink()
        spec=importlib.util.spec_from_file_location('backup',Path(__file__).with_name('backup-c4w.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.checksum(root)

if __name__=='__main__':main()
