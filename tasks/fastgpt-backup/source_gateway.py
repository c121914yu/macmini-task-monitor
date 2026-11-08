#!/opt/homebrew/bin/python3
"""Restricted Ubuntu backup key: stream only the existing read-only database exports."""
import os
import shlex
import subprocess
import sys

MONGO = ('exec mongodump --host=127.0.0.1 --port=27017 '
         '--username="$MONGO_INITDB_ROOT_USERNAME" --password="$MONGO_INITDB_ROOT_PASSWORD" '
         '--authenticationDatabase=admin --archive --gzip --oplog')
POSTGRES = ('export PGPASSWORD="$POSTGRES_PASSWORD"; '
            'exec pg_dumpall --host=127.0.0.1 --username="$POSTGRES_USER" --no-password')


def allowed(args):
    """Exact allowlist: no shell interpolation, arbitrary Docker exec or filesystem writes."""
    if len(args)==4 and args[:2]==['inspect','--format']:
        return ((args[2]=='{{json .State}}' and args[3]in('mongo','pg','fastgpt-rustfs'))
                or(args[2]in('{{json .Config.Env}}','{{.Config.Image}}') and args[3]=='fastgpt-rustfs'))
    return args in (['exec','mongo','sh','-eu','-c',MONGO],['exec','pg','sh','-eu','-c',POSTGRES])


def main():
    command=os.environ.get('SSH_ORIGINAL_COMMAND','')
    if command=='cat >/dev/null':
        while sys.stdin.buffer.read(65536):
            pass
        return
    try: args=shlex.split(command)
    except ValueError: args=[]
    if not args or args[0]!='/usr/local/bin/docker' or not allowed(args[1:]):
        print('Backup source command denied',file=sys.stderr)
        raise SystemExit(126)
    os.execv(args[0],args)


if __name__=='__main__':
    main()
