#!/usr/bin/python3
"""Route source reads to Mac mini, and only the temporary mc client to Ubuntu Docker."""
import json
import os
from pathlib import Path
import shlex
import sys


def route(args,config):
    """The exported databases are stdout streams; no backup file is written on Mac mini."""
    remote=(args and args[0]=='inspect') or (len(args)>1 and args[0]=='exec' and args[1]in('mongo','pg'))
    if remote:
        return [*config['ssh'],shlex.join(['/usr/local/bin/docker',*args])]
    args=list(args)
    if args and args[0]=='run':
        if '--network'not in args or args[args.index('--network')+1]!='container:fastgpt-rustfs':
            raise ValueError('Unexpected backup-client network')
        args[args.index('--network')+1]='host'
    return ['/usr/bin/sudo','-n','--preserve-env=MC_HOST_backup','/usr/bin/docker',*args]


def main():
    config=json.loads((Path(__file__).parent/'transport.json').read_text())
    if 'MC_HOST_backup'in os.environ:
        value=os.environ['MC_HOST_backup']
        suffix='@127.0.0.1:9000'
        if not value.endswith(suffix):raise RuntimeError('Unexpected source endpoint')
        os.environ['MC_HOST_backup']=value[:-len(suffix)]+'@127.0.0.1:'+str(config['port'])
    args=route(sys.argv[1:],config)
    os.execvpe(args[0],args,os.environ)


if __name__=='__main__':
    main()
