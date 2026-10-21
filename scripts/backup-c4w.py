#!/usr/bin/env python3
"""Export C4W and its CKAN dependencies from dev, with owned Azure media.

No production access. Source SQL uses one read-only repeatable-read snapshot.
The private bundle contains credentials and personal data: never publish it.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

REMOTE = r'''
import configparser, datetime, hashlib, html, io, json, os, re, subprocess, sys, tarfile, tempfile
from pathlib import Path
from urllib.parse import urlsplit, unquote
import psycopg2
from psycopg2 import sql
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions
os.umask(0o077)
p=configparser.ConfigParser(interpolation=None); p.read('/srv/app/production.ini'); config=p['app:main']
dsn=os.environ.get('CKAN_SQLALCHEMY_URL') or config['sqlalchemy.url']
u=urlsplit(dsn)
env=dict(os.environ, PGHOST=u.hostname, PGPORT=str(u.port or 5432), PGDATABASE=u.path.lstrip('/'), PGUSER=unquote(u.username or ''), PGPASSWORD=unquote(u.password or ''))
conn=psycopg2.connect(dsn, options='-c default_transaction_read_only=on', connect_timeout=15)
conn.set_session(isolation_level='REPEATABLE READ', readonly=True)
cur=conn.cursor()
cur.execute('SELECT pg_export_snapshot()'); snapshot=cur.fetchone()[0]
with tempfile.TemporaryDirectory(prefix='c4w-backup-') as tmp:
 root=Path(tmp); (root/'database').mkdir(); (root/'media').mkdir(); (root/'private').mkdir()
 (root/'private'/'production.ini').write_text(Path('/srv/app/production.ini').read_text())
 (root/'private'/'environment.json').write_text(json.dumps({k:v for k,v in os.environ.items() if k.upper().startswith(('CKAN','SOLR','REDIS'))}, indent=2))
 with (root/'database'/'schema.sql').open('wb') as out:
  subprocess.run(['pg_dump','--schema-only','--no-owner','--no-privileges','--snapshot='+snapshot],env=env,stdout=out,check=True)
 cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'c4w_%' ORDER BY tablename")
 tables=[r[0] for r in cur.fetchall()]
 rows_by_table={}; counts={}; urls=set(); users=set(); groups=set()
 def scan(value):
  if isinstance(value,dict):
   for v in value.values(): scan(v)
  elif isinstance(value,list):
   for v in value: scan(v)
  elif isinstance(value,str):
   for found in re.findall(r'https?://[^\s<>"\x27\\]+',html.unescape(value)):
    if '.blob.core.windows.net/' in found: urls.add(found)
 for table in tables:
  cur.execute(sql.SQL('SELECT row_to_json(t) FROM {} t').format(sql.Identifier(table)))
  rows=[r[0] for r in cur.fetchall()]; rows_by_table[table]=rows; counts[table]=len(rows)
  for row in rows:
   for key in ('created_by','author_id','uploaded_by','user_id','reviewed_by','manager_reviewed_by','submitted_by','modified_by','granted_by'):
    if row.get(key): users.add(row[key])
   if row.get('ckan_org_id'): groups.add(row['ckan_org_id'])
   if table=='c4w_relation':
    for side in ('subject','object'):
     if row.get(side+'_type')=='user' and row.get(side+'_id'): users.add(row[side+'_id'])
   scan(row)
 def select(table, where, params):
  cur.execute(sql.SQL('SELECT row_to_json(t) FROM {} t WHERE ').format(sql.Identifier(table))+sql.SQL(where),params)
  return [r[0] for r in cur.fetchall()]
 dependencies={}
 dependencies['member']=select('member',"group_id = ANY(%s) AND table_name = 'user'",(list(groups),)) if groups else []
 users.update(r['table_id'] for r in dependencies['member'])
 dependencies['user']=select('user','id = ANY(%s)',(list(users),)) if users else []
 dependencies['group']=select('group','id = ANY(%s)',(list(groups),)) if groups else []
 dependencies['group_extra']=select('group_extra','group_id = ANY(%s)',(list(groups),)) if groups else []
 for dep_rows in dependencies.values():
  for row in dep_rows: scan(row)
 # Export a COPY stream using PostgreSQL's native representations, including bytea.
 with (root/'database'/'data.sql').open('w') as out:
  out.write('BEGIN;\nSET session_replication_role = replica;\n')
  for table in ['user','group','group_extra','member']+tables:
   rows=rows_by_table.get(table,dependencies.get(table,[])); counts[table]=len(rows)
   cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',('public',table))
   columns=[r[0] for r in cur.fetchall()]
   names=sql.SQL(', ').join(map(sql.Identifier,columns))
   target=sql.SQL('{} ({})').format(sql.Identifier(table),names)
   if table in tables:
    query=sql.SQL('SELECT {} FROM {}').format(names,sql.Identifier(table))
   else:
    query=sql.SQL('SELECT {} FROM {} WHERE id = ANY({})').format(names,sql.Identifier(table),sql.Literal([r['id'] for r in rows]))
   buf=io.StringIO(); cur.copy_expert(sql.SQL('COPY ({}) TO STDOUT').format(query).as_string(conn),buf)
   out.write('COPY '+target.as_string(conn)+' FROM stdin;\n'+buf.getvalue()+'\\.\n')
  out.write('SET session_replication_role = origin;\nCOMMIT;\n')
 (root/'database'/'dependencies.json').write_text(json.dumps(dependencies,indent=2,default=str))
 (root/'database'/'counts.json').write_text(json.dumps(counts,indent=2))
 missing={'users':sorted(users-{r['id'] for r in dependencies['user']}),'organisations':sorted(groups-{r['id'] for r in dependencies['group']})}
 (root/'database'/'unresolved-references.json').write_text(json.dumps(missing,indent=2))
 conn.rollback(); conn.close()
 print('Database snapshot exported: %d C4W tables.' % len(tables),file=sys.stderr,flush=True)
 opts=json.loads(config['ckanext.asset_storage.backend_options'])
 service=BlobServiceClient.from_connection_string(opts['connection_string'])
 account=urlsplit(service.url).hostname
 media=[]
 for url in sorted(urls):
  parsed=urlsplit(url)
  if parsed.hostname!=account: raise RuntimeError('Media uses an unexpected Azure account')
  container,blob=unquote(parsed.path).lstrip('/').split('/',1)
  client=service.get_blob_client(container,blob)
  key=hashlib.sha256((container+'/'+blob).encode()).hexdigest()
  target=root/'media'/key
  for attempt in range(3):
   try:
    props=client.get_blob_properties()
    with target.open('wb') as output:
     client.download_blob(etag=props.etag,match_condition=MatchConditions.IfNotModified).readinto(output)
    after=client.get_blob_properties()
    if props.etag!=after.etag: raise RuntimeError('Object changed during backup')
    break
   except Exception:
    if attempt==2: raise
  digest=hashlib.sha256(target.read_bytes()).hexdigest()
  if target.stat().st_size!=props.size: raise RuntimeError('Incomplete media object')
  media.append(dict(url=url,container=container,blob=blob,path='media/'+key,size=props.size,sha256=digest,etag=props.etag,content_type=props.content_settings.content_type))
 print('Azure media verified: %d objects.' % len(media),file=sys.stderr,flush=True)
 (root/'media.json').write_text(json.dumps(media,indent=2))
 (root/'snapshot.json').write_text(json.dumps({'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'postgres_snapshot':snapshot,'c4w_tables':tables,'counts':counts,'media_count':len(media),'consistency':'One PostgreSQL read-only snapshot; Azure objects verified with ETags, not atomic across services.'},indent=2))
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as tar:
  for child in root.iterdir(): tar.add(child,arcname=child.name)
'''


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='final')
    args = parser.parse_args()
    os.umask(0o077)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    root = Path(__file__).resolve().parents[1]
    dest = root/'output'/'backups'/('c4w-dev-'+stamp+'-'+args.label)
    dest.mkdir(parents=True,mode=0o700)
    kube = ['kubectl','--context','default','-n','ckan']
    partial = dest/'snapshot.tar.gz.partial'
    with partial.open('wb') as out:
        run(kube+['exec','-i','deploy/ckan','-c','ckan','--','python','-'],input=REMOTE.encode(),stdout=out)
    archive=partial.with_suffix('');partial.rename(archive)
    with tarfile.open(archive) as tar:
        # Only our generated regular files/directories inside the temporary root.
        for item in tar.getmembers():
            if item.name.startswith('/') or '..' in Path(item.name).parts or item.issym() or item.islnk():
                raise RuntimeError('Unsafe archive member')
        tar.extractall(dest, filter='data')
    archive.unlink()
    private=dest/'private';private.mkdir(exist_ok=True)
    resources={}
    for kind,names in [('deployment',['ckan']),('statefulset',['postgres','solr','redis-master']),('ingress',['ckan-ingress']),('service',['ckan','postgres','solr','redis-master']),('pvc',['ckan'])]:
        output=subprocess.check_output(kube+['get',kind]+names+['-o','json'])
        (private/(kind+'.json')).write_bytes(output)
        resources[kind]=json.loads(output)
    selector=','.join(k+'='+v for k,v in resources['deployment']['spec']['selector']['matchLabels'].items())
    pod=json.loads(subprocess.check_output(kube+['get','pods','-l',selector,'-o','json']))
    (private/'pods.json').write_text(json.dumps(pod,indent=2))
    deployment=resources['deployment']; spec=deployment['spec']['template']['spec']
    secret_names=set()
    for c in spec.get('containers',[])+spec.get('initContainers',[]):
        for e in c.get('env',[]):
            ref=e.get('valueFrom',{}).get('secretKeyRef',{})
            if ref.get('name'):secret_names.add(ref['name'])
        for e in c.get('envFrom',[]):
            if e.get('secretRef',{}).get('name'):secret_names.add(e['secretRef']['name'])
    if secret_names:
        (private/'referenced-secrets.json').write_bytes(subprocess.check_output(kube+['get','secret']+sorted(secret_names)+['-o','json']))
    (dest/'source-status.txt').write_bytes(subprocess.check_output(['git','status','--short']))
    (dest/'source-head.txt').write_bytes(subprocess.check_output(['git','rev-parse','HEAD']))
    with tarfile.open(dest/'source.tar.gz','w:gz') as tar:
        files=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=root).decode().splitlines()
        for path in files:
            if (root/path).is_file():tar.add(root/path,arcname=path)
    with (dest/'deployed-source.tar.gz').open('wb') as out:
        run(kube+['exec','deploy/ckan','-c','ckan','--','tar','czf','-','--exclude=.git','--exclude=__pycache__','-C','/app/src','ckanext-c4w'],stdout=out)
    with (dest/'local-storage.tar.gz').open('wb') as out:
        run(kube+['exec','deploy/ckan','-c','ckan','--','tar','czf','-','-C','/var/lib/ckan','default'],stdout=out)
    (dest/'RESTORE.md').write_text((root/'docs'/'c4w-backup-restore.md').read_text())
    checksum(dest)
    print(str(dest),flush=True)


def checksum(dest):
    lines=[]
    for path in sorted(dest.rglob('*')):
        if not path.is_file() or path.name=='SHA256SUMS':continue
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
        lines.append(h.hexdigest()+'  '+str(path.relative_to(dest)))
    (dest/'SHA256SUMS').write_text('\n'.join(lines)+'\n')

if __name__=='__main__':main()
