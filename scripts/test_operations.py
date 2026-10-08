import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

FAKE_DOCKER = """#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["STACK"])
args = sys.argv[2:]
with (root / "events").open("a") as log:
    log.write(" ".join(args) + "\\n")
if args[0] == "stop":
    (root / "stopped").touch()
elif args[0] == "start":
    (root / "stopped").unlink(missing_ok=True)
elif args[0] in ("exec", "run"):
    assert (root / "stopped").exists(), "web still running"
    if os.environ.get("FAIL") and os.environ["FAIL"] in " ".join(args):
        sys.exit(1)
    if args[0] == "exec":
        if "pg_dump" in " ".join(args):
            sys.stdout.buffer.write((root / "database").read_bytes())
        else:
            data = sys.stdin.buffer.read()
            if data != b"valid dump":
                sys.exit(1)
            if "--file=/dev/null" not in args:
                (root / "database").write_bytes(data)
    elif "tar" in args:
        sys.exit(subprocess.call(["tar", "-C", str(root / "documents"), "-czf", "-", "."]))
    else:
        documents = root / "documents"
        for item in documents.iterdir():
            if item.is_dir():
                import shutil
                shutil.rmtree(item)
            else:
                item.unlink()
        sys.exit(subprocess.call(["tar", "-C", str(documents), "-xf", "-"]))
"""


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        for name in ("backup.sh", "restore.sh"):
            shutil.copy(Path(__file__).parent / name, self.root / "scripts" / name)
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.stack = self.root / "stack"
        self.stack.mkdir()
        (self.stack / "documents").mkdir()
        (self.stack / "documents" / "current.pdf").write_bytes(b"current")
        (self.stack / "database").write_bytes(b"current database")
        self.env = {
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "STACK": str(self.stack),
        }
        self.backup = self.root / "saved"
        self.backup.mkdir()
        (self.backup / "db.dump").write_bytes(b"valid dump")
        with tarfile.open(self.backup / "documents.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("restored.pdf")
            info.size = 8
            archive.addfile(info, io.BytesIO(b"restored"))

    def run_script(self, name, *args):
        return subprocess.run(
            ["bash", str(self.root / "scripts" / name), *map(str, args)],
            env=self.env,
            capture_output=True,
        )

    def assert_resumed(self):
        self.assertFalse((self.stack / "stopped").exists())
        self.assertTrue((self.stack / "events").read_text().endswith("start web\n"))
        self.assertEqual(list(self.root.glob(".restore.*")), [])

    def test_corrupt_restore_inputs_preserve_live_data(self):
        for corrupt in ("db.dump", "documents.tar.gz"):
            with self.subTest(corrupt=corrupt):
                path = self.backup / corrupt
                original = path.read_bytes()
                path.write_bytes(original[:5])
                result = self.run_script("restore.sh", self.backup, "--force")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.stack / "database").read_bytes(), b"current database")
                self.assertEqual(
                    (self.stack / "documents" / "current.pdf").read_bytes(), b"current"
                )
                self.assert_resumed()
                path.write_bytes(original)

    def test_restore_replaces_both_parts_while_stopped(self):
        result = self.run_script("restore.sh", self.backup, "--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.stack / "database").read_bytes(), b"valid dump")
        self.assertEqual(
            list((self.stack / "documents").iterdir()), [self.stack / "documents" / "restored.pdf"]
        )
        self.assertEqual((self.stack / "documents" / "restored.pdf").read_bytes(), b"restored")
        self.assert_resumed()

    def test_backup_copies_both_parts_while_stopped(self):
        (self.stack / "database").write_bytes(b"valid dump")
        result = self.run_script("backup.sh", self.root / "backups")
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next((self.root / "backups").iterdir())
        self.assertEqual((backup / "db.dump").read_bytes(), b"valid dump")
        with tarfile.open(backup / "documents.tar.gz") as archive:
            self.assertEqual(archive.extractfile("./current.pdf").read(), b"current")
        self.assert_resumed()

    def test_operation_failures_resume_web(self):
        for script, failure, args in (
            ("backup.sh", "pg_dump", [self.root / "backups"]),
            ("backup.sh", "tar", [self.root / "backups"]),
            ("restore.sh", "--clean", [self.backup, "--force"]),
            ("restore.sh", "--entrypoint sh", [self.backup, "--force"]),
        ):
            with self.subTest(script=script, failure=failure):
                self.env["FAIL"] = failure
                result = self.run_script(script, *args)
                self.assertNotEqual(result.returncode, 0)
                self.assert_resumed()


class AdminTests(unittest.TestCase):
    def test_only_account_models_are_registered(self):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        os.environ.setdefault("DJANGO_DEBUG", "true")
        import django

        django.setup()
        from django.contrib import admin
        from django.contrib.auth.models import Group, User

        from tracker.models import Application, ApplicationEvent, Company, Document

        for model in (Application, ApplicationEvent, Company, Document):
            self.assertFalse(admin.site.is_registered(model))
        for model in (User, Group):
            self.assertTrue(admin.site.is_registered(model))


if __name__ == "__main__":
    unittest.main()
