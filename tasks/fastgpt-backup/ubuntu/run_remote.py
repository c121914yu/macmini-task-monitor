#!/usr/bin/python3
"""One Ubuntu backup run with a private SSH tunnel to Mac mini's RustFS endpoint."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent


def main():
    os.umask(0o077)
    def stop(signum,_):
        raise SystemExit(128+signum)
    for signum in (signal.SIGTERM,signal.SIGINT):
        signal.signal(signum,stop)
    with (ROOT/'transport.lock').open('a')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        config=json.loads((ROOT/'transport.json').read_text())
        # The remote cat exits on stdin EOF, including when this parent is killed.
        args=[*config['ssh'][:-1],'-o','ExitOnForwardFailure=yes','-L',
              f'127.0.0.1:{config["port"]}:127.0.0.1:9000',config['ssh'][-1],'cat >/dev/null']
        with (ROOT/'transport.stderr.log').open('ab')as errors:
            process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=errors)
            try:
                deadline=time.monotonic()+30
                while True:
                    if process.poll()is not None:raise RuntimeError('Backup source SSH tunnel failed')
                    try:
                        with socket.create_connection(('127.0.0.1',config['port']),timeout=.5):break
                    except OSError:
                        if time.monotonic()>deadline:raise RuntimeError('Backup source tunnel timeout')
                        time.sleep(.2)
                result=subprocess.run(['/usr/bin/python3',str(ROOT/'backup.py')],check=False)
                if result.returncode:raise SystemExit(result.returncode)
            finally:
                process.stdin.close()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    main()
