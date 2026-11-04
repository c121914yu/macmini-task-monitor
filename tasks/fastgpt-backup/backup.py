#!/opt/homebrew/bin/python3
"""每天备份本机 FastGPT 的 MongoDB、PostgreSQL 和 RustFS，保留最近 7 个日历日。"""

import datetime as dt
import fcntl
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import zipfile
import uuid
from urllib.parse import quote
from zoneinfo import ZoneInfo


RUNTIME = Path(__file__).resolve().parent
BACKUP_ROOT = Path("/Volumes/code/backups/fastgpt")
TIMEZONE = ZoneInfo("Asia/Shanghai")
KEEP_DAYS = 7
DOCKER = "/usr/local/bin/docker"
CONTAINERS = {"mongodb": "mongo", "postgresql": "pg", "object_storage": "fastgpt-rustfs"}
CLIENT_IMAGE = "registry.cn-hangzhou.aliyuncs.com/fastgpt/minio:RELEASE.2025-09-07T16-13-09Z"
OWNER = "fastgpt-daily-backup-v1"
LOG = logging.getLogger("fastgpt-backup")


def run(args, *, output=None, timeout=7200, env=None):
    """执行容器命令；默认只把输出写入私有日志，配置导出直接写入备份文件。"""
    with LOG_PATH.open("ab") as errors:
        result = subprocess.run(
            [DOCKER, *args],
            stdout=output if output is not None else errors,
            stderr=errors,
            check=False,
            timeout=timeout,
            env=env,
        )
    if result.returncode:
        # 不打印完整命令，避免将配置、密码或其他敏感值带入调度器输出。
        raise RuntimeError(f"Docker {args[0]} 执行失败，退出码 {result.returncode}；详情见 {LOG_PATH}")


def capture(args):
    """捕获结构化输出；失败信息进入日志，不向调用方回显潜在敏感内容。"""
    with tempfile.TemporaryFile() as output:
        run(args, output=output, timeout=120)
        output.seek(0)
        return output.read().decode("utf-8")


def backup_databases(staging):
    """MongoDB 全量导出携带 oplog；PG 导出全部库与角色，避免遗漏 FastGPT 数据。"""
    LOG.info("开始 MongoDB 全量备份（包含 oplog）")
    with (staging / "mongodb.archive.gz").open("xb") as output:
        run([
            "exec", CONTAINERS["mongodb"], "sh", "-eu", "-c",
            'exec mongodump --host=127.0.0.1 --port=27017 '
            '--username="$MONGO_INITDB_ROOT_USERNAME" '
            '--password="$MONGO_INITDB_ROOT_PASSWORD" '
            '--authenticationDatabase=admin --archive --gzip --oplog',
        ], output=output)

    LOG.info("MongoDB 导出完成，开始 PostgreSQL 全部数据库与角色备份")
    # 先生成 SQL 再压缩会额外占用大量空间，直接流式压缩并检查 pg_dumpall 退出码。
    with (staging / "postgresql.sql.gz").open("xb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", compresslevel=6) as output:
            with LOG_PATH.open("ab") as errors:
                with subprocess.Popen([
                    DOCKER, "exec", CONTAINERS["postgresql"], "sh", "-eu", "-c",
                    'export PGPASSWORD="$POSTGRES_PASSWORD"; '
                    'exec pg_dumpall --host=127.0.0.1 --username="$POSTGRES_USER" --no-password',
                ], stdout=subprocess.PIPE, stderr=errors) as process:
                    try:
                        shutil.copyfileobj(process.stdout, output, length=1024 * 1024)
                        if process.wait(timeout=120):
                            raise RuntimeError("pg_dumpall 失败，保留已有备份；详情见当日日志")
                    except BaseException:
                        process.kill()
                        process.wait()
                        raise
    LOG.info("PostgreSQL 导出完成")


def backup_object_storage(staging):
    """使用临时只读客户端导出 RustFS；对象、桶、IAM、服务配置及原校验标准全部保留。"""
    container = CONTAINERS["object_storage"]
    values = dict(item.split("=", 1) for item in json.loads(
        capture(["inspect", "--format", "{{json .Config.Env}}", container])))
    if not values.get("RUSTFS_ACCESS_KEY") or not values.get("RUSTFS_SECRET_KEY"):
        raise RuntimeError("RustFS 根凭据未配置，不能形成完整备份")
    # 仅通过子进程环境向 Docker 传入；不放入参数、源代码或日志。
    credentials = {**os.environ, "MC_HOST_backup": "http://" +
        quote(values["RUSTFS_ACCESS_KEY"], safe="") + ":" +
        quote(values["RUSTFS_SECRET_KEY"], safe="") + "@127.0.0.1:9000"}
    image = json.loads(capture(["image", "inspect", "--format", "{{json .}}", CLIENT_IMAGE]))
    if image["Os"] != "linux" or image["Architecture"] not in ("amd64", "arm64"):
        raise RuntimeError("未确认备份客户端镜像的平台")
    client = "fastgpt-backup-client-" + uuid.uuid4().hex
    target = staging / "rustfs"
    target.mkdir(mode=0o700)
    (target / "objects").mkdir(mode=0o700)
    started = False

    def mc(*args, output=None, read=False):
        """复用原 mc 数据导出流程；客户端目录绑定本次私有备份目录。"""
        command = ["exec", "-w", "/export", client, "mc", "--no-color", *args]
        if read:
            return capture(command)
        run(command, output=output)

    def list_objects():
        records = [json.loads(line) for line in mc("ls", "--recursive", "--json", "backup", read=True).splitlines()]
        if any(item.get("status") != "success" for item in records):
            raise RuntimeError("对象存储列表读取失败")
        return sorted(
            [{"key": item["key"], "size": item["size"], "etag": item.get("etag", "")}
             for item in records if item.get("type") == "file"],
            key=lambda item: item["key"],
        )

    try:
        run(["run", "--detach", "--rm", "--platform", image["Os"] + "/" + image["Architecture"],
             "--name", client, "--label", "com.yjl.fastgpt-backup.owner=" + OWNER,
             "--user", str(os.getuid()) + ":" + str(os.getgid()),
             "--network", "container:" + container, "--env", "MC_HOST_backup",
             "--env", "MC_CONFIG_DIR=/tmp/backup-config",
             "--mount", "type=bind,source=" + str(target) + ",target=/export",
             "--workdir", "/export", "--entrypoint", "/bin/sh", image["Id"],
             "-eu", "-c", "umask 077; exec sleep infinity"], env=credentials)
        started = True
        buckets = [json.loads(line) for line in mc("ls", "--json", "backup", read=True).splitlines()]
        for bucket in buckets:
            if bucket.get("status") != "success" or bucket.get("type") != "folder":
                raise RuntimeError("对象存储桶列表格式异常")
            name = bucket["key"].rstrip("/")
            version = json.loads(mc("version", "info", "--json", f"backup/{name}", read=True))
            # mirror 只备份当前版本；以后启用版本控制时必须显式报错，不能把部分数据当成全备。
            if version.get("status") != "success" or version.get("versioning", {}).get("status") != "":
                raise RuntimeError(f"对象存储桶 {name} 开启过版本控制，需要改用包含历史版本的备份方案")

        LOG.info("开始对象存储在线备份：%s 个桶", len(buckets))
        before = list_objects()
        mc("mirror", "--json", "--preserve", "backup", "/export/objects")
        after = list_objects()
        if before != after:
            raise RuntimeError("对象存储对象在复制期间发生变化，本次备份不发布，保留已有备份")

        mc("admin", "cluster", "bucket", "export", "backup")
        mc("admin", "cluster", "iam", "export", "backup")
        with (target / "server-config.txt").open("xb") as output:
            mc("admin", "config", "export", "backup", output=output)
        (target / "buckets.json").write_text(json.dumps(buckets, ensure_ascii=False, indent=2) + "\n")
        (target / "objects.json").write_text(json.dumps(after, ensure_ascii=False, indent=2) + "\n")
        # 文件系统副本不能完整表达 S3 属性，额外保存 Content-Type、自定义元数据与对象标签。
        with (staging / "rustfs-object-metadata.jsonl").open("xb") as output:
            mc("stat", "--recursive", "--json", "backup", output=output)
        with (staging / "rustfs-object-tags.jsonl").open("xb") as output:
            mc("tag", "list", "--recursive", "--json", "backup", output=output)

        for item in after:
            path = target / "objects" / item["key"]
            if not path.is_file() or path.stat().st_size != item["size"]:
                raise RuntimeError(f"对象存储对象缺失或长度不符：{item['key']}")
            # 单段上传的 ETag 是 MD5，多段上传含 '-'，交由 mc 的传输校验处理。
            if re.fullmatch(r"[a-fA-F0-9]{32}", item["etag"]):
                with path.open("rb") as content:
                    if hashlib.file_digest(content, "md5").hexdigest() != item["etag"].lower():
                        raise RuntimeError(f"对象存储对象校验不符：{item['key']}")
        archives = list(target.glob("*.zip"))
        if len(archives) != 2:
            raise RuntimeError("对象存储桶配置或 IAM 导出文件缺失")
        for archive in archives:
            with zipfile.ZipFile(archive) as content:
                if content.testzip() is not None:
                    raise RuntimeError("对象存储元数据 ZIP 校验失败")
        LOG.info("对象存储备份及校验完成：%s 个对象", len(after))
        return {"engine": "rustfs", "container": container,
                "server_image": capture(["inspect", "--format", "{{.Config.Image}}", container]).strip(),
                "client_image": image["Id"], "object_count": len(after),
                "buckets": [item["key"].rstrip("/") for item in buckets]}
    finally:
        if started:
            # 只删除本函数创建并持有的 UUID 客户端；不停止 RustFS 或其他容器。
            run(["rm", "--force", client], timeout=120)


def cleanup_clients():
    """持有任务锁后回收上次进程异常退出留下的专用客户端，不碰业务容器。"""
    names = capture(["ps", "-a", "--filter", "label=com.yjl.fastgpt-backup.owner=" + OWNER,
                     "--format", "{{.Names}}"]).splitlines()
    for name in names:
        if not re.fullmatch(r"fastgpt-backup-client-[a-f0-9]{32}", name):
            raise RuntimeError("备份客户端归属不明确，拒绝删除")
        run(["rm", "--force", name], timeout=120)

def verify_and_manifest(staging, today, started_at, object_storage):
    """检查压缩流完整性，生成带 SHA256 的清单；成功标记最后写入。"""
    for name in ("mongodb.archive.gz", "postgresql.sql.gz"):
        path = staging / name
        if path.stat().st_size == 0:
            raise RuntimeError(f"导出文件为空：{name}")
        tail = b""
        with gzip.open(path, "rb") as content:
            while chunk := content.read(4 * 1024 * 1024):
                tail = (tail + chunk)[-1024:]
        if name == "postgresql.sql.gz" and b"PostgreSQL database cluster dump complete" not in tail:
            raise RuntimeError("PostgreSQL 导出缺少完成标记")

    files = []
    for path in sorted(staging.rglob("*")):
        if path.is_file():
            with path.open("rb") as content:
                checksum = hashlib.file_digest(content, "sha256").hexdigest()
            files.append({"path": str(path.relative_to(staging)), "bytes": path.stat().st_size, "sha256": checksum})
    manifest = {
        "owner": OWNER, "status": "complete", "date": today.isoformat(),
        "started_at": started_at, "completed_at": dt.datetime.now(TIMEZONE).isoformat(),
        "timezone": "Asia/Shanghai", "containers": CONTAINERS, "object_storage": object_storage, "files": files,
        "mongo_restore": "mongorestore --gzip --archive --oplogReplay",
        "postgres_restore": "gzip -dc postgresql.sql.gz | psql -X -U <superuser> -d postgres",
        "object_storage_restore": "启动与清单兼容的 RustFS，先导入 rustfs/ 中的桶配置，再 mc mirror rustfs/objects/ TARGET/；根据 rustfs-object-metadata.jsonl 与 rustfs-object-tags.jsonl 还原属性和标签，最后导入 IAM 与服务配置。",
        "consistency": "各组件在线分别备份，不提供三个存储之间的同一事务快照；对象存储只支持未启用版本控制的桶。",
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def prune_backups(root, today):
    """只清理本任务已完成且早于最近 7 个日历日的目录；忽略陌生目录及符号链接。"""
    cutoff = today - dt.timedelta(days=KEEP_DAYS - 1)
    for path in root.iterdir():
        if path.is_symlink() or not path.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name):
            continue
        try:
            day = dt.date.fromisoformat(path.name)
            manifest = json.loads((path / "manifest.json").read_text())
        except (ValueError, OSError):
            continue
        if day < cutoff and manifest.get("owner") == OWNER and manifest.get("status") == "complete":
            shutil.rmtree(path)
            LOG.info("已删除过期备份：%s", path.name)


def main():
    """持有进程锁完成三组件备份，校验后原子发布；失败不删除旧备份。"""
    os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: sys.exit(128 + signum))
    with (RUNTIME / "backup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("已有备份任务运行，本次跳过")
            return
        cleanup_clients()
        started_at = dt.datetime.now(TIMEZONE).isoformat()
        today = dt.datetime.now(TIMEZONE).date()
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        # SIGKILL 或断电不会触发 finally；下次持锁运行时清理本任务留下的半成品。
        for path in BACKUP_ROOT.glob(".????-??-??.partial-*"):
            if path.is_dir() and not path.is_symlink() and (path / ".backup-owner").is_file():
                if (path / ".backup-owner").read_text().strip() == OWNER:
                    shutil.rmtree(path)
        target = BACKUP_ROOT / today.isoformat()
        if target.exists():
            if target.is_symlink():
                raise RuntimeError("今日备份目录是符号链接，拒绝操作")
            manifest = json.loads((target / "manifest.json").read_text())
            if manifest.get("owner") != OWNER or manifest.get("status") != "complete":
                raise RuntimeError("今日目录已存在但不是本任务的完整备份，拒绝覆盖")
            LOG.info("今日备份目录已存在，保留现有数据，本次跳过")
            prune_backups(BACKUP_ROOT, today)
            return
        for container in CONTAINERS.values():
            state = json.loads(capture(["inspect", "--format", "{{json .State}}", container]))
            if not state.get("Running") or state.get("Paused"):
                raise RuntimeError(f"容器未运行或已暂停：{container}")

        # 临时目录与最终目录位于同一文件系统，只有全部校验成功才原子重命名。
        staging = Path(tempfile.mkdtemp(prefix=f".{today.isoformat()}.partial-", dir=BACKUP_ROOT))
        (staging / ".backup-owner").write_text(OWNER + "\n")
        try:
            backup_databases(staging)
            object_storage = backup_object_storage(staging)
            LOG.info("开始压缩流完整性及 SHA256 校验")
            verify_and_manifest(staging, today, started_at, object_storage)
            staging.rename(target)
            LOG.info("备份成功：%s", target)
            prune_backups(BACKUP_ROOT, today)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

        cutoff = today - dt.timedelta(days=KEEP_DAYS - 1)
        for path in (RUNTIME / "logs").glob("????-??-??.log"):
            if path.is_file() and not path.is_symlink() and dt.date.fromisoformat(path.stem) < cutoff:
                path.unlink()


if __name__ == "__main__":
    (RUNTIME / "logs").mkdir(mode=0o700, exist_ok=True)
    LOG_PATH = RUNTIME / "logs" / f"{dt.datetime.now(TIMEZONE).date().isoformat()}.log"
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        main()
    except BaseException:
        LOG.exception("备份任务失败，未发布不完整备份")
        raise
