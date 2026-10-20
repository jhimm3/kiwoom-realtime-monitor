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
