"""The checkout auto-update: fast-forward only, refusals leave everything as it was."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from maxsurf_mcp import autoupdate

GIT = shutil.which("git")


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid",
         "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@unittest.skipUnless(GIT, "git is not installed")
class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        self.upstream = self.tmp / "upstream"
        self.checkout = self.tmp / "checkout"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        git(self.tmp, "clone", "-q", str(self.origin), str(self.upstream))
        self.commit_upstream({"server.py": "v1\n", "requirements.lock": "a==1\n"})
        git(self.tmp, "clone", "-q", str(self.origin), str(self.checkout))
        self.server_file = str(self.checkout / "server.py")
        self.state = Path(git(self.checkout, "rev-parse", "--absolute-git-dir")) / "maxsurf-mcp"

    def commit_upstream(self, files: dict[str, str]) -> str:
        for name, text in files.items():
            (self.upstream / name).write_text(text, encoding="utf-8")
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-q", "-m", "change")
        git(self.upstream, "push", "-q", "origin", "main")
        return git(self.upstream, "rev-parse", "HEAD")

    def head(self) -> str:
        return git(self.checkout, "rev-parse", "HEAD")

    def start(self):
        with contextlib.redirect_stderr(io.StringIO()):
            startup = autoupdate.start(self.server_file)
        def release():
            for handle in (startup and startup.lock, startup and startup.instance_lock):
                if handle and not handle.closed:
                    autoupdate._unlock(handle)
        self.addCleanup(release)
        return startup

    def test_fast_forwards_to_origin_main_and_registers_the_instance(self):
        target = self.commit_upstream({"server.py": "v2\n"})
        startup = self.start()
        self.assertEqual(startup.update["status"], "updated")
        self.assertTrue(startup.needs_rerun())
        self.assertEqual(self.head(), target)
        self.assertEqual((self.checkout / "server.py").read_text(), "v2\n")
        startup.finish()
        self.assertIsNone(startup.lock)
        report = autoupdate.status(self.checkout)
        [record] = report["instances"]
        self.assertEqual((record["pid"], record["commit"], record["branch"]), (os.getpid(), target, "main"))
        self.assertTrue(record["alive"] and record["current"])
        self.assertIn('"status": "updated"', (self.state / "update.log").read_text())

    def test_finish_loads_every_package_module(self):
        startup = self.start()
        startup.finish()
        import pkgutil
        import maxsurf_mcp
        names = {f"maxsurf_mcp.{m.name}" for m in pkgutil.iter_modules(maxsurf_mcp.__path__)}
        self.assertIn("maxsurf_mcp.transfer", names)
        self.assertTrue({"maxsurf_mcp.transfer", "maxsurf_mcp.config"} <= set(sys.modules))

    def test_up_to_date_checkout_is_left_alone(self):
        before = self.head()
        self.assertEqual(self.start().update["status"], "up_to_date")
        self.assertEqual(self.head(), before)

    def test_tracked_local_changes_block_the_update(self):
        before = self.head()
        self.commit_upstream({"server.py": "v2\n"})
        (self.checkout / "server.py").write_text("local edit\n", encoding="utf-8")
        update = self.start().update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("local changes", update["reason"])
        self.assertEqual(self.head(), before)
        self.assertEqual((self.checkout / "server.py").read_text(), "local edit\n")

    def test_a_feature_branch_is_never_updated(self):
        git(self.checkout, "switch", "-q", "-c", "feature")
        before = self.head()
        self.commit_upstream({"server.py": "v2\n"})
        update = self.start().update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("feature", update["reason"])
        self.assertEqual(self.head(), before)

    def test_diverged_main_is_never_rewritten(self):
        (self.checkout / "local.txt").write_text("x", encoding="utf-8")
        git(self.checkout, "add", "local.txt")
        git(self.checkout, "commit", "-q", "-m", "local")
        local = self.head()
        self.commit_upstream({"server.py": "v2\n"})
        update = self.start().update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("not an ancestor", update["reason"])
        self.assertEqual(self.head(), local)

    def test_unreachable_origin_starts_on_current_code(self):
        before = self.head()
        git(self.checkout, "remote", "set-url", "origin", str(self.tmp / "missing.git"))
        update = self.start().update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("fetch failed", update["reason"])
        self.assertEqual(self.head(), before)

    def test_dependency_change_waits_while_another_server_runs(self):
        before = self.head()
        self.commit_upstream({"requirements.lock": "a==2\n"})
        other = self.register_live_instance()
        with mock.patch.object(autoupdate, "_install_requirements") as install:
            update = self.start().update
        install.assert_not_called()
        self.assertEqual(update["status"], "deferred")
        self.assertIn(str(other.pid), update["reason"])
        self.assertEqual(self.head(), before)

    def test_dependency_change_installs_before_fast_forward(self):
        target = self.commit_upstream({"requirements.lock": "a==2\n"})
        heads = []
        with mock.patch.object(autoupdate, "_install_requirements",
                               side_effect=lambda root, rev: heads.append((self.head(), rev)) or (True, "")):
            update = self.start().update
        self.assertEqual(update["status"], "updated")
        self.assertTrue(update["dependencies_changed"])
        self.assertNotEqual(heads[0][0], target)  # installed while still on the old commit
        self.assertEqual(heads[0][1], target)
        self.assertEqual(self.head(), target)

    def test_failed_dependency_install_keeps_the_old_commit(self):
        before = self.head()
        self.commit_upstream({"requirements.lock": "a==2\n"})
        with mock.patch.object(autoupdate, "_install_requirements", return_value=(False, "boom")):
            update = self.start().update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("boom", update["reason"])
        self.assertEqual(self.head(), before)

    def test_dead_instance_records_do_not_block_and_are_pruned(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (self.state / "instances").mkdir(parents=True)
        record = self.state / "instances" / f"{dead.pid}.json"
        record.write_text(json.dumps({"pid": dead.pid}))
        self.assertFalse(autoupdate.pid_alive(dead.pid))
        self.assertTrue(autoupdate.pid_alive(os.getpid()))
        target = self.commit_upstream({"requirements.lock": "a==2\n"})
        with mock.patch.object(autoupdate, "_install_requirements", return_value=(True, "")):
            self.assertEqual(self.start().update["status"], "updated")
        self.assertEqual(self.head(), target)
        self.assertFalse(record.exists())

    def test_a_version_bump_is_not_a_dependency_change(self):
        (self.upstream / "pyproject.toml").write_text(
            '[project]\nversion = "1"\ndependencies = ["a==1"]\n', encoding="utf-8")
        self.commit_upstream({})
        git(self.checkout, "pull", "-q")
        self.register_live_instance()
        target = self.commit_upstream({"pyproject.toml": '[project]\nversion = "2"\ndependencies = ["a==1"]\n'})
        with mock.patch.object(autoupdate, "_install_requirements") as install:
            update = self.start().update
        install.assert_not_called()
        self.assertEqual((update["status"], update["dependencies_changed"]), ("updated", False))
        self.assertEqual(self.head(), target)

    def test_a_pyproject_dependency_edit_is_a_dependency_change(self):
        self.register_live_instance()
        self.commit_upstream({"pyproject.toml": '[project]\ndependencies = ["a==2"]\n'})
        self.assertEqual(self.start().update["status"], "deferred")

    def test_an_untracked_file_in_the_way_blocks_before_anything_runs(self):
        before = self.head()
        self.commit_upstream({"new.txt": "from main\n"})
        (self.checkout / "new.txt").write_text("mine\n", encoding="utf-8")
        update = self.start().update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("new.txt", update["reason"])
        self.assertEqual(((self.checkout / "new.txt").read_text(), self.head()), ("mine\n", before))

    def test_line_ending_noise_is_not_a_local_change(self):
        git(self.checkout, "config", "core.autocrlf", "true")
        os.utime(self.checkout / "server.py", (1, 1))
        target = self.commit_upstream({"server.py": "v2\n"})
        self.assertEqual(self.start().update["status"], "updated")
        self.assertEqual(self.head(), target)

    def test_liveness_follows_the_instance_lock_not_the_pid(self):
        startup = self.start()
        startup.finish()
        [record] = autoupdate.status(self.checkout)["instances"]
        self.assertTrue(record["alive"])
        autoupdate._unlock(startup.instance_lock)  # what the OS does when the server dies
        self.assertEqual(autoupdate.status(self.checkout)["instances"], [])
        self.assertFalse(startup.record_path.exists())

    def test_malformed_records_are_ignored(self):
        (self.state / "instances").mkdir(parents=True)
        for name, body in {"1": "null", "2": '{"pid": null}', "3": "{bad", "4": '{"pid": "x"}'}.items():
            (self.state / "instances" / f"{name}.json").write_text(body)
        self.assertEqual(autoupdate.status(self.checkout)["instances"], [])
        self.assertEqual(self.start().update["status"], "up_to_date")

    def register_live_instance(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        (self.state / "instances").mkdir(parents=True, exist_ok=True)
        (self.state / "instances" / f"{other.pid}.json").write_text(json.dumps({"pid": other.pid}))
        return other

    def test_a_held_lock_skips_the_update_instead_of_racing(self):
        before = self.head()
        self.commit_upstream({"server.py": "v2\n"})
        held = autoupdate.acquire_lock(self.state / "update.lock", 0)
        self.addCleanup(autoupdate._unlock, held)
        with mock.patch.object(autoupdate, "LOCK_WAIT_S", 0.3):
            startup = self.start()
        self.assertEqual(startup.update["status"], "skipped")
        self.assertIsNone(startup.lock)
        self.assertEqual(self.head(), before)

    def test_waits_for_a_pip_left_running_by_a_killed_server(self):
        before = self.head()
        self.commit_upstream({"server.py": "v2\n"})
        pip = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(pip.wait)
        self.addCleanup(pip.kill)
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "installing.pid").write_text(str(pip.pid))
        with mock.patch.object(autoupdate, "INSTALL_TIMEOUT_S", 0.5):
            first = self.start()
        first.finish()  # releases the update lock, as the server does after its imports
        update = first.update
        self.assertEqual(update["status"], "skipped")
        self.assertIn("still running", update["reason"])
        self.assertEqual(self.head(), before)
        pip.kill()
        pip.wait()
        self.assertEqual(self.start().update["status"], "updated")
        self.assertFalse((self.state / "installing.pid").exists())

    def test_installs_with_uv_into_this_interpreter_and_falls_back_to_pip(self):
        with mock.patch.object(autoupdate.shutil, "which", return_value="C:/bin/uv.exe"):
            self.assertEqual(autoupdate._installer(),
                             ["C:/bin/uv.exe", "pip", "install", "--python", sys.executable])
        with mock.patch.object(autoupdate.shutil, "which", return_value=None):
            self.assertEqual(autoupdate._installer()[:4], [sys.executable, "-m", "pip", "install"])

    def test_can_be_disabled(self):
        before = self.head()
        self.commit_upstream({"server.py": "v2\n"})
        with mock.patch.dict(os.environ, {"MAXSURF_MCP_AUTO_UPDATE": "0"}):
            self.assertEqual(self.start().update["status"], "disabled")
        self.assertEqual(self.head(), before)

    def test_outside_a_checkout_root_nothing_happens(self):
        bundle = self.checkout / "bundle"
        bundle.mkdir()
        self.assertIsNone(autoupdate.start(str(bundle / "server.py")))
        self.assertIsNone(autoupdate.start(str(self.tmp / "server.py")))


if __name__ == "__main__":
    unittest.main()
