#!/usr/bin/python3
"""Verify complete owned backup trees before publication, transfer confirmation or deletion."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

OWNER='fastgpt-daily-backup-v1'
ROOT=Path('/home/archer/backups/fastgpt')


def verify(path):
    """Reject symlinks, traversal and unknown files; verify the manifest and every payload."""
    path=Path(path)
    if path.is_symlink()or not path.is_dir():raise RuntimeError('Invalid backup directory')
    manifest=path/'manifest.json'
    if manifest.is_symlink():raise RuntimeError('Symlink manifest')
    data=json.loads(manifest.read_text())
    if data.get('owner')!=OWNER or data.get('status')!='complete':raise RuntimeError('Backup is not complete/owned')
    dt.date.fromisoformat(data['date'])
    names=set()
    for item in data['files']:
        relative=Path(item['path'])
        if relative.is_absolute()or '..'in relative.parts or str(relative)in names:raise RuntimeError('Invalid manifest path')
        names.add(str(relative))
        target=path/relative
        if any(p.is_symlink()for p in [target,*target.parents]if p!=path.parent):
            raise RuntimeError('Symlink backup payload')
        if not stat.S_ISREG(target.stat().st_mode)or target.stat().st_size!=item['bytes']:
            raise RuntimeError('Backup payload size mismatch')
        with target.open('rb')as content:
            if hashlib.file_digest(content,'sha256').hexdigest()!=item['sha256']:
                raise RuntimeError('Backup payload checksum mismatch')
    for target in path.rglob('*'):
        if target.is_symlink():raise RuntimeError('Unexpected symlink')
        if target.is_file()and str(target.relative_to(path))not in names|{'manifest.json','.backup-owner'}:
            raise RuntimeError('Unexpected backup payload')
    return data


def summary():
    records=[]
    if ROOT.exists():
        for path in sorted(ROOT.iterdir()):
            if path.is_symlink()or not path.is_dir()or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',path.name):continue
            p=path/'manifest.json'
            if p.is_symlink():raise RuntimeError('Symlink manifest')
            data=json.loads(p.read_text())
            if data.get('owner')!=OWNER or data.get('status')!='complete':continue
            records.append({'date':data['date'],'bytes':sum(f['bytes']for f in data['files']),
                'file_count':len(data['files']),'started_at':data['started_at'],'completed_at':data['completed_at'],
                'path':'archer-unbuntu26-dev:'+str(path),
                'manifest_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    return {'storage_host':'archer-unbuntu26-dev','storage_root':str(ROOT),
            'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'backups':records}


if __name__=='__main__':
    if len(sys.argv)>1:
        data=verify(Path(sys.argv[1]))
        print(json.dumps({'date':data['date'],'files':len(data['files']),'status':'verified',
                         'manifest_sha256':hashlib.sha256((Path(sys.argv[1])/'manifest.json').read_bytes()).hexdigest()}))
    else:print(json.dumps(summary(),ensure_ascii=False))
