"""真实临时备份文件 + 模拟容器边界验证 RustFS 适配，不访问业务数据。"""
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("backup_candidate", Path(__file__).with_name("backup.py"))
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = b"restorable-object"
        self.object = {"status": "success", "type": "file", "key": "bucket/file",
                       "size": len(self.body), "etag": hashlib.md5(self.body).hexdigest()}
        self.version = ""
        self.change = False
        self.corrupt = None
        self.reads = 0
        self.calls = []
        self.enterContext(patch.object(backup, "capture", self.capture))
        self.enterContext(patch.object(backup, "run", self.docker_run))

    def capture(self, args):
        if args[0] == "inspect":
            if ".Config.Env" in args[2]:
                return json.dumps(["RUSTFS_ACCESS_KEY=test-key", "RUSTFS_SECRET_KEY=a@:/+ secret"])
            return "rustfs/rustfs:1.0.4"
        if args[0] == "image":
            return json.dumps({"Os": "linux", "Architecture": "amd64", "Id": "sha256:local-client"})
        op = args[6:]
        if op == ["ls", "--json", "backup"]:
            return json.dumps({"status": "success", "type": "folder", "key": "bucket/"})
        if op[:2] == ["version", "info"]:
            return json.dumps({"status": "success", "versioning": {"status": self.version}})
        if op == ["ls", "--recursive", "--json", "backup"]:
            self.reads += 1
            value = dict(self.object)
            if self.change and self.reads == 2: value["etag"] = "changed"
            return json.dumps(value)
        raise AssertionError(args)

    def docker_run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] in ("run", "rm"): return
        op = args[6:]
        target = self.root / "rustfs"
        if op[0] == "mirror":
            p = target / "objects/bucket/file"
            p.parent.mkdir(parents=True)
            p.write_bytes(self.corrupt if self.corrupt is not None else self.body)
        elif op[:3] == ["admin", "cluster", "bucket"] or op[:3] == ["admin", "cluster", "iam"]:
            name = "bucket" if op[2] == "bucket" else "iam"
            with zipfile.ZipFile(target / (name + ".zip"), "w") as z: z.writestr(name + ".json", "{}")
        elif kwargs.get("output") is not None:
            kwargs["output"].write(b"{}\n")

    def assert_client_cleaned(self):
        cleanup = [args for args, _ in self.calls if args[:2] == ["rm", "--force"]]
        self.assertEqual(len(cleanup), 1)
        self.assertRegex(cleanup[0][2], r"^fastgpt-backup-client-[0-9a-f]{32}$")

    def test_objects_metadata_and_credentials_are_kept_in_correct_boundaries(self):
        result = backup.backup_object_storage(self.root)
        self.assertEqual(result["engine"], "rustfs")
        self.assertEqual(result["object_count"], 1)
        self.assertEqual((self.root / "rustfs/objects/bucket/file").read_bytes(), self.body)
        self.assertTrue((self.root / "rustfs-object-metadata.jsonl").exists())
        self.assertTrue((self.root / "rustfs-object-tags.jsonl").exists())
        args, kwargs = next(call for call in self.calls if call[0][0] == "run")
        self.assertNotIn("a@:/+ secret", str(args))
        self.assertIn("a%40%3A%2F%2B%20secret", kwargs["env"]["MC_HOST_backup"])
        self.assertIn("container:fastgpt-rustfs", args)
        self.assertIn("sha256:local-client", args)
        self.assert_client_cleaned()

    def test_versioned_bucket_fails_before_mirror_but_cleans_client(self):
        self.version = "Enabled"
        with self.assertRaisesRegex(RuntimeError, "版本控制"): backup.backup_object_storage(self.root)
        self.assertEqual(self.reads, 0)
        self.assert_client_cleaned()

    def test_object_changes_do_not_publish_partial_snapshot(self):
        self.change = True
        with self.assertRaisesRegex(RuntimeError, "复制期间发生变化"): backup.backup_object_storage(self.root)
        self.assertFalse((self.root / "rustfs/server-config.txt").exists())
        self.assert_client_cleaned()

    def test_wrong_size_or_hash_is_rejected(self):
        for body in (b"wrong-size", b"X" * len(self.body)):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp); self.corrupt = body; self.calls = []; self.reads = 0
                with self.assertRaisesRegex(RuntimeError, "不符"): backup.backup_object_storage(self.root)
                self.assert_client_cleaned()

    def test_manifest_records_restorable_backend_and_real_hashes(self):
        with gzip.open(self.root / "mongodb.archive.gz", "wb") as f: f.write(b"mongo-archive-fixture")
        with gzip.open(self.root / "postgresql.sql.gz", "wb") as f:
            f.write(b"-- PostgreSQL database cluster dump complete\n")
        evidence = {"engine": "rustfs", "container": "fastgpt-rustfs"}
        backup.verify_and_manifest(self.root, dt.date(2026, 11, 4), "start", evidence)
        manifest = json.loads((self.root / "manifest.json").read_text())
        self.assertEqual(manifest["object_storage"], evidence)
        self.assertIn("rustfs/objects", manifest["object_storage_restore"])
        self.assertEqual(manifest["status"], "complete")
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((self.root / item["path"]).read_bytes()).hexdigest(), item["sha256"])

    def test_cleanup_never_removes_business_container_even_if_label_matches(self):
        with patch.object(backup, "capture", return_value="fastgpt-rustfs\n"):
            with self.assertRaisesRegex(RuntimeError, "归属不明确"): backup.cleanup_clients()
        self.assertEqual(self.calls, [])


if __name__ == "__main__": unittest.main()
