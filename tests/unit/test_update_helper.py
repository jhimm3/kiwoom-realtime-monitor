from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import kiwoom_monitor.update_helper as update_helper

from kiwoom_monitor.update_helper import UpdateError, _read_manifest, _safe_relative, apply_archives, apply_update


class UpdateHelperTest(unittest.TestCase):
    def test_query_restart_uses_only_persistent_role_after_real_archive_apply(self) -> None:
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "앱 설치"
                target.mkdir()
                executable = target / "app.exe"
                archive = root / "update.zip"
                self._create_archive(archive, {"version.txt": "new"})
                data = root / "선택 프로필"
                options = {"query_only": True}
                expected = [str(executable), "--query-only"]
                if explicit:
                    options.update(query_data_dir=data, query_settings_role="fixture-query")
                    expected.extend(("--query-data-dir", str(data), "--query-settings-role", "fixture-query"))
                def launch(*args, **kwargs):
                    self.assertEqual(b"new", (target / "version.txt").read_bytes())
                    self.assertFalse(archive.exists())
                with patch.object(update_helper, "UpdateProgress"), patch.object(update_helper, "_wait_for_process"), \
                     patch.object(update_helper.subprocess, "Popen", side_effect=launch) as restart, \
                     patch.object(update_helper.time, "sleep"), patch.object(update_helper.sys, "argv", ["helper", "--token", "secret"]):
                    apply_update(123, (archive,), target, executable, root / "apply.log", **options)
                restart.assert_called_once_with(expected, cwd=str(target))

    def test_helper_cli_preserves_typed_role_and_rejects_partial_or_transient_arguments(self) -> None:
        import sys
        base = ["helper", "--wait-pid", "123", "--archive", "update.zip", "--target", "app", "--exe", "app/app.exe", "--log", "apply.log"]
        role = ["--query-only", "--query-data-dir", "chosen", "--query-settings-role", "fixture-query"]
        for arguments in ([], ["--query-only"], role):
            with self.subTest(arguments=arguments), patch.object(sys, "argv", base + arguments), patch.object(update_helper, "apply_update") as apply:
                self.assertEqual(0, update_helper.main())
                apply.assert_called_once_with(123, (Path("update.zip"),), Path("app"), Path("app/app.exe"), Path("apply.log"),
                    query_only=bool(arguments), query_data_dir=Path("chosen") if arguments == role else None,
                    query_settings_role="fixture-query" if arguments == role else None)
        for arguments in (["--query-data-dir", "chosen"], ["--query-only", "--query-settings-role", "fixture-query"], role + ["--token", "secret"]):
            with self.subTest(invalid=arguments), patch.object(sys, "argv", base + list(arguments)), patch.object(update_helper, "apply_update") as apply, patch.object(update_helper.sys, "stderr"):
                with self.assertRaises(SystemExit) as error:
                    update_helper.main()
                self.assertEqual(2, error.exception.code)
                apply.assert_not_called()

    def test_full_restart_preserves_only_explicit_root_and_role_after_real_archive_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "앱 설치"
            target.mkdir()
            executable = target / "app.exe"
            archive = root / "update.zip"
            self._create_archive(archive, {"version.txt": "new"})
            data = root / "선택 전체 실행"
            def launch(*args, **kwargs):
                self.assertEqual(b"new", (target / "version.txt").read_bytes())
                self.assertFalse(archive.exists())
            with patch.object(update_helper, "UpdateProgress"), patch.object(update_helper, "_wait_for_process"), \
                 patch.object(update_helper.subprocess, "Popen", side_effect=launch) as restart, \
                 patch.object(update_helper.time, "sleep"), patch.object(update_helper.sys, "argv", ["helper", "--token", "secret", "--restore-once"]):
                apply_update(123, (archive,), target, executable, root / "apply.log",
                             full_data_dir=data, full_settings_role="fixture-full")
            restart.assert_called_once_with([str(executable), "--full-data-dir", str(data),
                                            "--full-settings-role", "fixture-full"], cwd=str(target))

    def test_full_role_rejects_partial_empty_and_query_mixtures_before_any_update_effect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "version.txt").write_bytes(b"original")
            archive = root / "update.zip"
            self._create_archive(archive, {"version.txt": "new"})
            data = root / "chosen"
            options = (
                {"full_data_dir": data}, {"full_settings_role": "fixture-full"},
                {"full_data_dir": "", "full_settings_role": "fixture-full"},
                {"full_data_dir": "  ", "full_settings_role": "fixture-full"},
                {"full_data_dir": data, "full_settings_role": ""},
                {"full_data_dir": data, "full_settings_role": "  "},
                {"full_data_dir": data, "full_settings_role": "fixture-full", "query_only": True},
                {"full_data_dir": data, "full_settings_role": "fixture-full", "query_data_dir": data},
                {"full_data_dir": data, "full_settings_role": "fixture-full", "query_settings_role": "fixture-query"},
                {"full_data_dir": data, "full_settings_role": "fixture-full", "query_only": True,
                 "query_data_dir": data, "query_settings_role": "fixture-query"},
            )
            before = self._snapshot(root)
            for arguments in options:
                with self.subTest(arguments=arguments), patch.object(update_helper, "UpdateProgress") as progress, \
                     patch.object(update_helper, "_wait_for_process") as wait, patch.object(update_helper, "apply_archives") as apply, \
                     patch.object(update_helper, "_write_log") as log, patch.object(update_helper.subprocess, "Popen") as restart:
                    with self.assertRaises(ValueError):
                        apply_update(123, (archive,), target, target / "app.exe", root / "apply.log", **arguments)
                    for effect in (progress, wait, apply, log, restart):
                        effect.assert_not_called()
                    self.assertEqual(before, self._snapshot(root))

    def test_helper_cli_preserves_full_role_and_rejects_empty_partial_and_query_mixtures(self) -> None:
        base = ["helper", "--wait-pid", "123", "--archive", "update.zip", "--target", "app", "--exe", "app/app.exe", "--log", "apply.log"]
        full = ["--full-data-dir", "선택 전체 실행", "--full-settings-role", "fixture-full"]
        with patch.object(update_helper.sys, "argv", base + full), patch.object(update_helper, "apply_update") as apply:
            self.assertEqual(0, update_helper.main())
            apply.assert_called_once_with(123, (Path("update.zip"),), Path("app"), Path("app/app.exe"), Path("apply.log"),
                                          full_data_dir=Path("선택 전체 실행"), full_settings_role="fixture-full")
        invalid = (["--full-data-dir", "chosen"], ["--full-settings-role", "fixture-full"],
                   ["--full-data-dir", "", "--full-settings-role", "fixture-full"],
                   ["--full-data-dir", "  ", "--full-settings-role", "fixture-full"],
                   ["--full-data-dir", "chosen", "--full-settings-role", ""],
                   ["--full-data-dir", "chosen", "--full-settings-role", "  "],
                   full + ["--query-only"], full + ["--query-data-dir", "chosen"],
                   full + ["--query-settings-role", "fixture-query"],
                   full + ["--query-only", "--query-data-dir", "chosen", "--query-settings-role", "fixture-query"],
                   full + ["--token", "secret"])
        for arguments in invalid:
            with self.subTest(invalid=arguments), patch.object(update_helper.sys, "argv", base + arguments), \
                 patch.object(update_helper, "apply_update") as apply, patch.object(update_helper.sys, "stderr"):
                with self.assertRaises(SystemExit) as error:
                    update_helper.main()
                self.assertEqual(2, error.exception.code)
                apply.assert_not_called()

    @staticmethod
    def _snapshot(root: Path) -> tuple[dict[str, bytes], set[str]]:
        return (
            {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()},
            {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()},
        )

    @staticmethod
    def _create_archive(path: Path, changed: dict[str, str], deleted: tuple[str, ...] = ()) -> None:
        manifest = {"version": "test", "changed": list(changed), "deleted": list(deleted)}
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("update_manifest.json", json.dumps(manifest))
            for name, content in changed.items():
                archive.writestr(name, content)

    def test_safe_relative_rejects_path_escape(self) -> None:
        self.assertEqual(Path("_internal") / "module.pyd", _safe_relative("_internal/module.pyd"))
        for value in ("../outside.txt", "/outside.txt", "C:/outside.txt", ""):
            with self.assertRaises(UpdateError):
                _safe_relative(value)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            outside = root / "outside.txt"
            outside.write_text("preserved", encoding="utf-8")
            archive = root / "escape.zip"
            self._create_archive(archive, {"../outside.txt": "overwritten"})
            with self.assertRaises(UpdateError):
                apply_archives((archive,), target)
            self.assertEqual("preserved", outside.read_text(encoding="utf-8"))

    def test_manifest_rejects_changed_file_outside_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            (staging / "update_manifest.json").write_text(
                json.dumps({"changed": ["../outside.txt"], "deleted": []}), encoding="utf-8"
            )
            with self.assertRaises(UpdateError):
                _read_manifest(staging)

    def test_apply_archives_applies_multiple_versions_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "version.txt").write_text("1.1.1", encoding="utf-8")
            first = root / "1.1.2.zip"
            second = root / "1.1.3.zip"
            third = root / "1.1.4.zip"
            self._create_archive(first, {"version.txt": "1.1.2", "features/one.txt": "one"})
            self._create_archive(second, {"version.txt": "1.1.3", "features/two.txt": "two"}, ("features/one.txt",))
            self._create_archive(third, {"version.txt": "1.1.4", "features/three.txt": "three"})

            apply_archives((first, second, third), target)

            self.assertEqual("1.1.4", (target / "version.txt").read_text(encoding="utf-8"))
            self.assertFalse((target / "features" / "one.txt").exists())
            self.assertEqual("two", (target / "features" / "two.txt").read_text(encoding="utf-8"))
            self.assertEqual("three", (target / "features" / "three.txt").read_text(encoding="utf-8"))
            self.assertFalse(first.exists())
            self.assertFalse(second.exists())
            self.assertFalse(third.exists())

    def test_apply_archives_rolls_back_multiple_archives_after_copy_or_delete_failure(self) -> None:
        for operation in ("copy", "delete"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "app"
                target.mkdir()
                for name in ("version.txt", "a.txt", "b.txt", "obsolete-one.txt", "obsolete-two.txt"):
                    (target / name).write_text(f"old-{name}", encoding="utf-8")
                (target / "user.db").write_bytes(b"user database preserved")
                (target / "empty").mkdir()
                user_data = root / "user-data"
                user_data.mkdir()
                (user_data / "settings.db").write_bytes(b"external user data preserved")
                first, second = root / "first.zip", root / "second.zip"
                self._create_archive(first, {"version.txt": "new-one", "created/one.txt": "new-added"}, ("obsolete-one.txt",))
                self._create_archive(second, {"version.txt": "new-two", "a.txt": "new-a", "b.txt": "new-b"}, ("obsolete-two.txt",))
                before = self._snapshot(target)
                archives_before = (first.read_bytes(), second.read_bytes())
                copy2, unlink = update_helper.shutil.copy2, Path.unlink
                failures: list[str] = []

                def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
                    if operation == "copy" and Path(source).name == "a.txt" and Path(source).read_bytes() == b"new-a" and not failures:
                        Path(destination).write_bytes(b"partial copy")
                        failures.append("copy")
                        raise OSError("injected copy failure")
                    return copy2(source, destination, *args, **kwargs)

                def delete(path: Path, *args: object, **kwargs: object) -> None:
                    if operation == "delete" and path == target / "obsolete-two.txt" and not failures:
                        failures.append("delete")
                        raise OSError("injected delete failure")
                    unlink(path, *args, **kwargs)

                with patch.object(update_helper.shutil, "copy2", side_effect=copy), patch.object(Path, "unlink", delete):
                    with self.assertRaisesRegex(OSError, f"injected {operation} failure"):
                        apply_archives((first, second), target)

                self.assertEqual([operation], failures)
                self.assertEqual(before, self._snapshot(target))
                self.assertEqual(archives_before, (first.read_bytes(), second.read_bytes()))
                self.assertEqual(b"external user data preserved", (user_data / "settings.db").read_bytes())
                self.assertEqual({"app", "user-data", "first.zip", "second.zip"}, {path.name for path in root.iterdir()})

    def test_apply_archives_rolls_back_first_and_last_copy_and_delete_failures(self) -> None:
        for operation, failed_name in (("copy", "a"), ("copy", "c"), ("delete", "x"), ("delete", "z")):
            with self.subTest(operation=operation, failed_name=failed_name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "app"
                target.mkdir()
                for name in ("a", "b", "c", "x", "y", "z", "user.db"):
                    (target / name).write_text(f"old-{name}", encoding="utf-8")
                archive = root / "update.zip"
                self._create_archive(archive, {"a": "new-a", "b": "new-b", "c": "new-c"}, ("x", "y", "z"))
                before = self._snapshot(target)
                copy2, unlink = update_helper.shutil.copy2, Path.unlink
                failed: list[str] = []

                def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
                    if operation == "copy" and Path(source).name == failed_name and Path(source).read_bytes().startswith(b"new-") and not failed:
                        Path(destination).write_bytes(b"partial copy")
                        failed.append(operation)
                        raise OSError("injected boundary failure")
                    return copy2(source, destination, *args, **kwargs)

                def delete(path: Path, *args: object, **kwargs: object) -> None:
                    if operation == "delete" and path == target / failed_name and not failed:
                        failed.append(operation)
                        raise OSError("injected boundary failure")
                    unlink(path, *args, **kwargs)

                with patch.object(update_helper.shutil, "copy2", side_effect=copy), patch.object(Path, "unlink", delete):
                    with self.assertRaisesRegex(OSError, "injected boundary failure"):
                        apply_archives((archive,), target)

                self.assertEqual([operation], failed)
                self.assertEqual(before, self._snapshot(target))
                self.assertTrue(archive.is_file())
                self.assertEqual({"app", "update.zip"}, {path.name for path in root.iterdir()})

    def test_apply_archives_validates_entire_batch_before_changing_installation(self) -> None:
        for invalid in ("missing", "broken-zip", "manifest", "missing-payload", "deleted-escape"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "app"
                target.mkdir()
                (target / "version.txt").write_text("old", encoding="utf-8")
                first, second = root / "first.zip", root / "second.zip"
                self._create_archive(first, {"version.txt": "new-one"})
                if invalid == "broken-zip":
                    second.write_bytes(b"invalid zip")
                elif invalid == "manifest":
                    with zipfile.ZipFile(second, "w") as archive:
                        archive.writestr("update_manifest.json", "[]")
                elif invalid == "missing-payload":
                    with zipfile.ZipFile(second, "w") as archive:
                        archive.writestr("update_manifest.json", json.dumps({"changed": ["absent.txt"]}))
                elif invalid == "deleted-escape":
                    self._create_archive(second, {}, ("../outside.txt",))
                before = self._snapshot(target)
                archives_before = {path.name: path.read_bytes() for path in (first, second) if path.exists()}

                with self.assertRaises(UpdateError):
                    apply_archives((first, second), target)

                self.assertEqual(before, self._snapshot(target))
                self.assertEqual(archives_before, {path.name: path.read_bytes() for path in (first, second) if path.exists()})
                self.assertEqual({"app", *archives_before}, {path.name for path in root.iterdir()})

    def test_apply_archives_backup_failure_leaves_originals_and_archives_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "a").write_bytes(b"old-a")
            (target / "b").write_bytes(b"old-b")
            archive = root / "update.zip"
            self._create_archive(archive, {"a": "new-a", "b": "new-b"})
            before = self._snapshot(target)
            copy2 = update_helper.shutil.copy2
            copies: list[Path] = []

            def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
                copies.append(Path(source))
                if Path(source) == target / "b":
                    Path(destination).write_bytes(b"partial backup")
                    raise OSError("backup unavailable")
                return copy2(source, destination, *args, **kwargs)

            with patch.object(update_helper.shutil, "copy2", side_effect=copy):
                with self.assertRaisesRegex(OSError, "backup unavailable"):
                    apply_archives((archive,), target)

            self.assertEqual([target / "a", target / "b"], copies)
            self.assertEqual(before, self._snapshot(target))
            self.assertTrue(archive.is_file())
            self.assertEqual({"app", "update.zip"}, {path.name for path in root.iterdir()})

    def test_apply_archives_backs_up_union_once_and_uses_same_folder_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "version.txt").write_bytes(b"old")
            (target / "obsolete.txt").write_bytes(b"obsolete")
            (target / "user.db").write_bytes(b"preserved")
            first, second = root / "first.zip", root / "second.zip"
            self._create_archive(first, {"version.txt": "one", "features/one.txt": "one"}, ("obsolete.txt",))
            self._create_archive(second, {"version.txt": "two", "features/two.txt": "two"}, ("features/one.txt",))
            copy2, replace = update_helper.shutil.copy2, Path.replace
            backups: list[tuple[str, int]] = []
            replacements: list[str] = []

            def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
                source = Path(source)
                if source.is_relative_to(target):
                    backups.append((source.relative_to(target).as_posix(), source.stat().st_size))
                return copy2(source, destination, *args, **kwargs)

            def atomic_replace(source: Path, destination: Path) -> Path:
                self.assertEqual(source.parent, destination.parent)
                self.assertTrue(source.name.startswith(".kiwoom-update-"))
                self.assertEqual(".tmp", source.suffix)
                self.assertTrue(first.is_file() and second.is_file())
                replacements.append(destination.relative_to(target).as_posix())
                return replace(source, destination)

            with patch.object(update_helper.shutil, "copy2", side_effect=copy), patch.object(Path, "replace", atomic_replace):
                apply_archives((first, second), target)

            self.assertEqual([("version.txt", 3), ("obsolete.txt", 8)], backups)
            self.assertEqual(["version.txt", "features/one.txt", "version.txt", "features/two.txt"], replacements)
            self.assertEqual(({"version.txt": b"two", "features/two.txt": b"two", "user.db": b"preserved"}, {"features"}), self._snapshot(target))
            self.assertEqual({"app"}, {path.name for path in root.iterdir()})

    def test_rollback_restores_file_removed_before_later_archive_creates_children(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "shared").write_bytes(b"old shared file")
            (target / "last.txt").write_bytes(b"old last file")
            first, second = root / "first.zip", root / "second.zip"
            self._create_archive(first, {}, ("shared",))
            self._create_archive(second, {"shared/child.txt": "child", "last.txt": "new last file"})
            before = self._snapshot(target)
            copy2 = update_helper.shutil.copy2

            def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
                if Path(source).name == "last.txt" and Path(source).read_bytes() == b"new last file":
                    raise OSError("last copy unavailable")
                return copy2(source, destination, *args, **kwargs)

            with patch.object(update_helper.shutil, "copy2", side_effect=copy):
                with self.assertRaisesRegex(OSError, "last copy unavailable"):
                    apply_archives((first, second), target)

            self.assertEqual(before, self._snapshot(target))
            self.assertEqual({"app", "first.zip", "second.zip"}, {path.name for path in root.iterdir()})

    def test_apply_update_preserves_recovery_and_never_restarts_after_apply_failure(self) -> None:
        for rollback_failure in (False, True):
            with self.subTest(rollback_failure=rollback_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "app"
                target.mkdir()
                for name in ("a", "b", "c"):
                    (target / name).write_text(f"old-{name}", encoding="utf-8")
                archive = root / "update.zip"
                self._create_archive(archive, {"a": "new-a", "b": "new-b", "c": "new-c"})
                before = self._snapshot(target)
                copy2 = update_helper.shutil.copy2

                def copy(source: Path, destination: Path, *args: object, **kwargs: object) -> object:
                    source = Path(source)
                    if source.name == "c" and source.read_bytes() == b"new-c":
                        raise OSError("apply failed")
                    if rollback_failure and source.name == "b" and "originals" in source.parts:
                        raise OSError("restore failed")
                    return copy2(source, destination, *args, **kwargs)

                with patch.object(update_helper.shutil, "copy2", side_effect=copy), patch.object(update_helper, "UpdateProgress"), patch.object(update_helper, "_wait_for_process") as wait, patch.object(update_helper.subprocess, "Popen") as restart, patch.object(update_helper.messagebox, "showerror") as showerror:
                    with self.assertRaisesRegex(UpdateError if rollback_failure else OSError, "복구 자료" if rollback_failure else "apply failed") as raised:
                        apply_update(123, (archive,), target, target / "app.exe", root / "apply.log")
                    restart.assert_not_called()
                    wait.assert_called_once()
                    showerror.assert_called_once()

                self.assertTrue(archive.is_file())
                if rollback_failure:
                    self.assertEqual(b"old-a", (target / "a").read_bytes())
                    self.assertEqual(b"new-b", (target / "b").read_bytes())
                    self.assertEqual(b"old-c", (target / "c").read_bytes())
                    recovery_dirs = list(root.glob("staging-*"))
                    self.assertEqual(1, len(recovery_dirs))
                    recovery = recovery_dirs[0]
                    self.assertIn(str(recovery), str(raised.exception))
                    note = json.loads((recovery / "recovery.json").read_text(encoding="utf-8"))
                    self.assertEqual(str(target.resolve()), note["target"])
                    self.assertEqual(["a", "b"], note["applied"])
                    self.assertEqual(1, len(note["rollback_errors"]))
                    self.assertIn("restore failed", note["rollback_errors"][0])
                    for name in ("a", "b", "c"):
                        self.assertEqual(f"old-{name}".encode(), (recovery / note["originals"][name]).read_bytes())
                    self.assertEqual(b"new-c", (recovery / "1" / "c").read_bytes())
                else:
                    self.assertEqual(before, self._snapshot(target))
                    self.assertFalse(list(root.glob("staging-*")))

    def test_apply_update_restarts_once_only_after_complete_batch(self) -> None:
        for restart_failure in (False, True):
            with self.subTest(restart_failure=restart_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "app"
                target.mkdir()
                (target / "version.txt").write_bytes(b"old")
                first, second = root / "first.zip", root / "second.zip"
                self._create_archive(first, {"version.txt": "one"})
                self._create_archive(second, {"version.txt": "two"})
                executable = target / "app.exe"

                def launch(*args: object, **kwargs: object) -> None:
                    self.assertEqual(b"two", (target / "version.txt").read_bytes())
                    self.assertFalse(first.exists() or second.exists())
                    self.assertFalse(list(root.glob("staging-*")))
                    if restart_failure:
                        raise OSError("restart failed")

                with patch.object(update_helper, "UpdateProgress") as progress, patch.object(update_helper, "_wait_for_process"), patch.object(update_helper.subprocess, "Popen", side_effect=launch) as restart, patch.object(update_helper.time, "sleep"), patch.object(update_helper.messagebox, "showerror") as showerror:
                    if restart_failure:
                        with self.assertRaisesRegex(OSError, "restart failed"):
                            apply_update(123, (first, second), target, executable, root / "apply.log")
                        showerror.assert_called_once()
                    else:
                        apply_update(123, (first, second), target, executable, root / "apply.log")
                        showerror.assert_not_called()
                        progress.return_value.close.assert_called_once()
                    restart.assert_called_once_with([str(executable)], cwd=str(target))

    def test_archive_cleanup_failure_preserves_new_installation_and_never_restarts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "app"
            target.mkdir()
            (target / "version.txt").write_bytes(b"old")
            first, second = root / "first.zip", root / "second.zip"
            self._create_archive(first, {"version.txt": "one"})
            self._create_archive(second, {"version.txt": "two"})
            second_before = second.read_bytes()
            unlink = Path.unlink

            def delete(path: Path, *args: object, **kwargs: object) -> None:
                if path == second:
                    raise OSError("archive cleanup failed")
                unlink(path, *args, **kwargs)

            with patch.object(Path, "unlink", delete), patch.object(update_helper, "UpdateProgress"), patch.object(update_helper, "_wait_for_process"), patch.object(update_helper.subprocess, "Popen") as restart, patch.object(update_helper.messagebox, "showerror"):
                with self.assertRaisesRegex(UpdateError, "파일 적용은 완료됐지만"):
                    apply_update(123, (first, second), target, target / "app.exe", root / "apply.log")
                restart.assert_not_called()

            self.assertEqual(({"version.txt": b"two"}, set()), self._snapshot(target))
            self.assertFalse(first.exists())
            self.assertEqual(second_before, second.read_bytes())
            recovery_dirs = list(root.glob("staging-*"))
            self.assertEqual(1, len(recovery_dirs))
            recovery = recovery_dirs[0]
            note = json.loads((recovery / "recovery.json").read_text(encoding="utf-8"))
            self.assertEqual("archive_cleanup", note["phase"])
            self.assertEqual("new", note["product_state"])
            self.assertEqual([str(second.resolve())], note["remaining_archives"])
            self.assertEqual(b"old", (recovery / "originals" / "version.txt").read_bytes())

    def test_code_rollback_preserves_native_unknown_and_never_resubmits_after_restart(self) -> None:
        import sqlite3

        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.central_server.database import SQLiteQueryStore
        from kiwoom_monitor.domain.order_contract import OrderState
        from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
        from tests.unit.test_order_lifecycle import NOW, _Transport, _account, _intent

        for loss in ("external_response", "native_commit_ack"):
            with self.subTest(loss=loss), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "app"
                target.mkdir()
                # Source desktops and retained legacy data can be inside the
                # install root. The reviewed code archive does not list data/.
                database = target / "data" / "central.sqlite3"
                for name in ("a", "b", "c"):
                    (target / name).write_bytes(f"old-{name}".encode())
                archive = root / "update.zip"
                self._create_archive(archive, {"a": "new-a", "b": "new-b", "c": "new-c"})
                archive_before = archive.read_bytes()
                intent, account = _intent(), _account()

                def closed_native_snapshot():
                    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                    try:
                        connection.execute("PRAGMA query_only=ON")
                        self.assertEqual((1,), connection.execute("PRAGMA query_only").fetchone())
                        self.assertEqual([("ok",)], connection.execute("PRAGMA quick_check").fetchall())
                        self.assertEqual([], connection.execute("PRAGMA foreign_key_check").fetchall())
                        connection.execute("BEGIN")
                        intents = connection.execute(
                            "SELECT intent_id,run_id,environment,account_ref,state,document_json "
                            "FROM central_execution_intents ORDER BY intent_id",
                        ).fetchall()
                        events = connection.execute(
                            "SELECT accepted_sequence,event_id,intent_id,state,document_json "
                            "FROM central_execution_events ORDER BY accepted_sequence",
                        ).fetchall()
                        connection.rollback()
                    finally:
                        connection.close()
                    with self.assertRaises(sqlite3.ProgrammingError):
                        connection.execute("SELECT 1")
                    self.assertEqual(1, len(intents))
                    document = json.loads(intents[0][5])
                    self.assertEqual(
                        ("intent-1", "run-1", "mock", "account-1"), intents[0][:4],
                    )
                    self.assertEqual(
                        ("intent-1", "run-1", "mock", "account-1", "decision-1"),
                        tuple(document[key] for key in
                              ("intent_id", "run_id", "environment", "account_ref", "decision_id")),
                    )
                    self.assertEqual(intents[0][4], document["state"])
                    self.assertEqual(list(range(1, len(events) + 1)), [row[0] for row in events])
                    self.assertEqual(len(events), len({row[1] for row in events}))
                    for row in events:
                        event = json.loads(row[4])
                        self.assertTrue(row[1])
                        self.assertEqual((row[1], "intent-1", row[3]),
                                         (event["event_id"], event["intent_id"], event["state"]))
                        self.assertEqual("intent-1", row[2])
                    return {"intents": intents, "events": events}

                store = SQLiteQueryStore(database)
                try:
                    store.initialize()
                    repository = ExecutionRepository(store)
                    transport = _Transport(SubmissionUnknown("controlled external response lost")) \
                        if loss == "external_response" else _Transport()
                    lifecycle = OrderLifecycle(repository, transport, now_provider=lambda: NOW)
                    lifecycle.queue(intent)
                    queued = closed_native_snapshot()
                    self.assertEqual("QUEUED", queued["intents"][0][4])
                    self.assertEqual(1, len(queued["events"]))
                    first_receipt = queued["events"][0]
                    self.assertEqual("QUEUED", json.loads(first_receipt[4])["event_type"])
                    self.assertEqual(intent.created_at.isoformat(), json.loads(first_receipt[4])["occurred_at"])
                    self.assertEqual(NOW.isoformat(), json.loads(first_receipt[4])["received_at"])
                    if loss == "native_commit_ack":
                        native_apply = repository.apply
                        lost_receipts = []

                        def lose_ack_after_commit(record, event, **changes):
                            result = native_apply(record, event, **changes)
                            if event.event_type == "SUBMISSION_STARTED":
                                lost_receipts.append(event.event_id)
                                raise OSError("controlled submission claim ACK lost after COMMIT")
                            return result

                        with patch.object(repository, "apply", side_effect=lose_ack_after_commit):
                            with self.assertRaisesRegex(OSError, "ACK lost after COMMIT"):
                                lifecycle.submit(intent.intent_id, account)
                        self.assertEqual(1, len(lost_receipts))
                        expected_types = ["QUEUED", "SUBMISSION_STARTED"]
                        expected_submit_calls = 0
                    else:
                        self.assertEqual(OrderState.SUBMISSION_UNKNOWN,
                                         lifecycle.submit(intent.intent_id, account).state)
                        expected_types = ["QUEUED", "SUBMISSION_STARTED", "SUBMISSION_RESPONSE_UNKNOWN"]
                        expected_submit_calls = 1
                    self.assertEqual(expected_submit_calls, transport.submit_calls)
                    durable = repository.load(intent.intent_id)
                    self.assertIsNotNone(durable)
                    self.assertEqual(intent, durable.intent)
                    self.assertEqual(OrderState.SUBMISSION_UNKNOWN, durable.state)
                    native_events = repository.events(intent.intent_id)
                    before = closed_native_snapshot()
                    self.assertEqual("SUBMISSION_UNKNOWN", before["intents"][0][4])
                    self.assertEqual(expected_types, [json.loads(row[4])["event_type"] for row in before["events"]])
                    self.assertEqual(first_receipt, before["events"][0])
                    self.assertEqual(list(native_events), [json.loads(row[4]) for row in before["events"]])
                    if loss == "native_commit_ack":
                        self.assertEqual(lost_receipts, [before["events"][-1][1]])
                finally:
                    store.close()

                # Every native connection above is closed before file apply.
                # Include the inside-target DB in the complete pre-helper tree.
                target_before = self._snapshot(target)
                copy2 = update_helper.shutil.copy2
                failed_copies = []

                def fail_last_code_copy(source, destination, *args, **kwargs):
                    source = Path(source)
                    if source.name == "c" and source.read_bytes() == b"new-c":
                        self.assertEqual(b"new-a", (target / "a").read_bytes())
                        self.assertEqual(b"new-b", (target / "b").read_bytes())
                        failed_copies.append("c")
                        raise OSError("controlled last code copy failed")
                    return copy2(source, destination, *args, **kwargs)

                with patch.object(update_helper.shutil, "copy2", side_effect=fail_last_code_copy), \
                     patch.object(update_helper, "UpdateProgress"), \
                     patch.object(update_helper, "_wait_for_process") as wait, \
                     patch.object(update_helper.subprocess, "Popen") as restart, \
                     patch.object(update_helper.messagebox, "showerror") as showerror:
                    with self.assertRaisesRegex(OSError, "controlled last code copy failed"):
                        apply_update(123, (archive,), target, target / "app.exe", root / "apply.log")
                    wait.assert_called_once()
                    restart.assert_not_called()
                    showerror.assert_called_once()
                self.assertEqual(["c"], failed_copies)
                self.assertEqual({name: f"old-{name}".encode() for name in ("a", "b", "c")},
                                 {name: (target / name).read_bytes() for name in ("a", "b", "c")})
                self.assertEqual(target_before, self._snapshot(target))
                self.assertEqual(archive_before, archive.read_bytes())
                self.assertFalse(list(root.glob("staging-*")))
                self.assertEqual(before, closed_native_snapshot())

                reopened = SQLiteQueryStore(database)
                try:
                    repository = ExecutionRepository(reopened)
                    replacement = _Transport()
                    restarted = OrderLifecycle(repository, replacement, now_provider=lambda: NOW)
                    self.assertEqual(durable, repository.load(intent.intent_id))
                    self.assertEqual(native_events, repository.events(intent.intent_id))
                    record = restarted.submit(intent.intent_id, account)
                    self.assertEqual(durable, record)
                    self.assertEqual(OrderState.SUBMISSION_UNKNOWN, record.state)
                    self.assertEqual((0, 0), (replacement.submit_calls, replacement.cancel_calls))
                    self.assertEqual(expected_submit_calls, transport.submit_calls)
                    self.assertEqual(native_events, repository.events(intent.intent_id))
                    after = closed_native_snapshot()
                    self.assertEqual(before, after)
                    self.assertEqual(first_receipt, after["events"][0])
                finally:
                    reopened.close()
