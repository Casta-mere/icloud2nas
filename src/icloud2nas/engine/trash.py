"""Trash-first deletion: library files are renamed under <library>/.icloud2nas, never unlinked.

Layout (spec §16.1): `<library>/.icloud2nas/trash/<YYYY-MM-DD>/<rel>` (day dirs carry
`.i2n-day`), `<library>/.icloud2nas/kept/<rel>`, and deleted libraries renamed to
`<parent>/.icloud2nas-deleted/<basename>-<YYYY-MM-DD>`. `walk_library` skips dot directories,
so none of this takes part in sync or reconcile.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import posixpath
import shutil
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

STORE_DIRNAME = ".icloud2nas"
TRASH_DIRNAME = "trash"
KEPT_DIRNAME = "kept"
DELETED_DIRNAME = ".icloud2nas-deleted"
DAY_MARKER = ".i2n-day"
LEGACY_TRASH_DIRNAME = ".trash"  # Plan 2: <photos_root>/.trash/<library_rel>/<day>
LEGACY_KEPT_DIRNAME = "kept"  # Plan 2: <photos_root>/kept/<library_rel>
CROSS_DEVICE = "cross-device: move the folder manually"


def _lib(library_dir: str | Path) -> Path:
    """An absolute library directory without '..' components (it need not exist)."""
    lib = Path(library_dir)
    if not lib.is_absolute() or ".." in lib.parts:
        raise ValueError(f"invalid library directory: {str(library_dir)!r}")
    return lib


def _inside(lib: Path, path: Path) -> bool:
    """Whether path, symlinks resolved, stays within lib (a symlinked store must not escape)."""
    return path.resolve().is_relative_to(lib.resolve())


def store_dir(library_dir: str | Path) -> Path:
    return _lib(library_dir) / STORE_DIRNAME


def trash_dir(library_dir: str | Path) -> Path:
    return store_dir(library_dir) / TRASH_DIRNAME


def kept_dir(library_dir: str | Path) -> Path:
    return store_dir(library_dir) / KEPT_DIRNAME


def trash_day_dir(library_dir: str | Path, day: date) -> Path:
    return trash_dir(library_dir) / day.isoformat()


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 1
    while True:
        candidate = dest.with_name(f"{dest.stem}~{n}{dest.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _is_day_dir(path: Path) -> bool:
    """A trash day dir is ISO-date-named AND carries the marker move_to_trash writes."""
    try:
        date.fromisoformat(path.name)
    except ValueError:
        return False
    return (path / DAY_MARKER).is_file()


def _copy_then_unlink(src: Path, dest: Path) -> None:
    """Cross-device fallback: copy to a temp name, move it into place, then drop the source."""
    tmp = dest.with_name(dest.name + ".part")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.unlink(src)


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
OnCreate = Callable[[int, Path], None]  # (fd of a directory just made, its path for messages)


def subdir(
    fd: int, name: str, shown: Path, *, create: bool = False, on_create: OnCreate | None = None
) -> int:
    """Open directory `name` of the open directory `fd` without following a symlink.

    Everything under a library folder is writable by its NAS user, so root never follows a link
    planted there (`.icloud2nas/kept/x -> /etc`) nor one swapped in after a check: ValueError
    when `name` is a symlink or not a directory. A missing one raises FileNotFoundError, or with
    `create` is made relative to `fd` and handed to `on_create`.
    """
    try:
        return os.open(name, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        if not create:
            raise
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(
                f"{str(shown)!r} is a symlink or not a folder; refusing to follow it"
            ) from exc
        raise
    made = True
    try:
        os.mkdir(name, dir_fd=fd)
    except FileExistsError:
        made = False  # someone else made it meanwhile; open it like any existing one
    new = subdir(fd, name, shown)
    if made and on_create is not None:
        try:
            on_create(new, shown)
        except BaseException:
            os.close(new)
            raise
    return new


def descend(
    fd: int,
    base: Path,
    parts: Iterable[str],
    *,
    create: bool = False,
    on_create: OnCreate | None = None,
) -> int:
    """fd of base/parts..., each component opened from the open directory `fd` (left open)
    with subdir. The caller closes the result."""
    cur = os.dup(fd)
    here = base
    try:
        for part in parts:
            here = here / part
            nxt = subdir(cur, part, here, create=create, on_create=on_create)
            os.close(cur)
            cur = nxt
    except BaseException:
        os.close(cur)
        raise
    return cur


def open_dir(
    root: Path, path: Path, *, create: bool = False, on_create: OnCreate | None = None
) -> int:
    """fd of path, opened one component at a time below root (itself opened normally) with
    O_NOFOLLOW; see subdir. ValueError when path is not below root."""
    rel = path.relative_to(root)  # ValueError when outside
    top = os.open(root, _DIR_FLAGS)
    try:
        return descend(top, root, rel.parts, create=create, on_create=on_create)
    finally:
        os.close(top)


def open_existing_dir(root: Path, path: Path) -> int:
    """open_dir of an existing path; a missing component is a ValueError too."""
    try:
        return open_dir(root, path)
    except FileNotFoundError as exc:
        raise ValueError(f"{str(path)!r} does not exist") from exc


def _link_move(src_fd: int | None, src: str, dst_fd: int, name: str) -> tuple[str | None, str]:
    """Move a file by link + unlink into the open directory dst_fd as `name`, or on a clash as
    `stem~1.suffix`, `stem~2.suffix`, ...: link never overwrites (EEXIST means try the next
    name) and never copies (EXDEV is an error).

    src is relative to src_fd (or a path when src_fd is None). Returns (error or None, the
    name used).
    """
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, n = name, 0
    while True:
        try:
            os.link(src, candidate, src_dir_fd=src_fd, dst_dir_fd=dst_fd, follow_symlinks=False)
            break
        except OSError as exc:
            if exc.errno != errno.EEXIST:
                err = "cross-device" if exc.errno == errno.EXDEV else (exc.strerror or str(exc))
                return err, candidate
        n += 1
        candidate = f"{stem}~{n}{suffix}"
    try:
        os.unlink(src, dir_fd=src_fd)
    except OSError as exc:  # linked but not removed: say so instead of a bare 500
        log.error("kept %s as %s but could not remove the trash copy: %s", src, candidate, exc)
        reason = exc.strerror or str(exc)
        return f"kept as {candidate!r}, but the trash copy was not removed: {reason}", candidate
    return None, candidate


def _move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dest)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        _copy_then_unlink(src, dest)


def move_to_trash(
    library_dir: str | Path, abs_path: str | Path, day: date | None = None
) -> Path:
    """Move a library file to the day's trash folder, preserving its path within the library."""
    lib = _lib(library_dir)
    src = Path(abs_path)
    rel = src.resolve().relative_to(lib.resolve())  # ValueError if outside
    if rel.parts and rel.parts[0] == STORE_DIRNAME:
        raise ValueError(f"refusing to trash a file of the {STORE_DIRNAME} store: {rel}")
    day_dir = trash_day_dir(lib, day or date.today())
    if not _inside(lib, day_dir):
        raise ValueError(f"trash folder escapes the library: {day_dir}")
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / DAY_MARKER).touch(exist_ok=True)
    dest = _unique(day_dir / rel)
    _move_file(src, dest)
    return dest


def _open_below(
    photos: str | Path,
    lib: Path,
    path: Path,
    *,
    create: bool = False,
    on_create: OnCreate | None = None,
) -> int:
    """fd of `path` inside the library `lib`, with lib itself reached from the photos root.

    Every component after the photos root is opened with O_NOFOLLOW, so a NAS user who owns a
    parent of their library cannot swap the library (or anything above it) for a link to
    another user's: ValueError. Only folders below lib are ever made (create); a missing lib,
    or a missing folder below it without create, is FileNotFoundError.
    """
    lib_fd = open_dir(Path(photos), lib)
    try:
        return descend(lib_fd, lib, path.relative_to(lib).parts, create=create, on_create=on_create)
    finally:
        os.close(lib_fd)


def _free_dir_name(fd: int, name: str) -> str:
    """name, or name~1, name~2, ... (whole-name suffix: directory names carry dates)."""
    candidate, n = name, 0
    while True:
        try:
            os.stat(candidate, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return candidate
        n += 1
        candidate = f"{name}~{n}"


def trash_library_dir(
    library_dir: str | Path,
    today: date,
    *,
    photos: str | Path,
    on_create: OnCreate | None = None,
) -> Path | None:
    """Rename a whole library folder to <parent>/.icloud2nas-deleted/<name>-<day>; None if absent.

    The parent is reached from the photos root by O_NOFOLLOW fds and the rename is relative to
    them; a symlinked component (or library) is refused. Rename only: a mount point, another
    filesystem or a busy folder raise ValueError and nothing moves. A `.icloud2nas-deleted`
    this call makes goes to on_create.
    """
    src = _lib(library_dir)
    try:
        parent_fd = open_dir(Path(photos), src.parent)
    except FileNotFoundError:
        return None
    try:
        try:
            st = os.stat(src.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError(f"{str(src)!r} is a symlink or not a folder; refusing to move it")
        if st.st_dev != os.fstat(parent_fd).st_dev or os.path.ismount(src):
            raise ValueError(f"{src} is a mount point; {CROSS_DEVICE}")
        try:
            os.stat(DELETED_DIRNAME, dir_fd=parent_fd, follow_symlinks=False)
            created = False
        except FileNotFoundError:
            created = True
        root = src.parent / DELETED_DIRNAME
        deleted_fd = subdir(parent_fd, DELETED_DIRNAME, root, create=True, on_create=on_create)
        try:
            name = _free_dir_name(deleted_fd, f"{src.name}-{today.isoformat()}")
            try:
                os.rename(src.name, name, src_dir_fd=parent_fd, dst_dir_fd=deleted_fd)
            except OSError as exc:
                if created:
                    with contextlib.suppress(OSError):
                        os.rmdir(DELETED_DIRNAME, dir_fd=parent_fd)
                if exc.errno == errno.EXDEV:
                    raise ValueError(f"{src}: {CROSS_DEVICE}") from exc
                if exc.errno == errno.EBUSY:
                    raise ValueError(f"{src} is busy: {exc.strerror}") from exc
                raise
        finally:
            os.close(deleted_fd)
    finally:
        os.close(parent_fd)
    return root / name


def restore_library_dir(trashed: str | Path, home: str | Path, *, photos: str | Path) -> None:
    """Undo trash_library_dir: rename `<parent>/.icloud2nas-deleted/<x>` back to home, through
    O_NOFOLLOW fds from the photos root; the deleted folder is removed when empty."""
    trashed, home = Path(trashed), Path(home)
    if trashed.parent != home.parent / DELETED_DIRNAME:
        raise ValueError(f"{str(trashed)!r} is not a trashed copy of {str(home)!r}")
    parent_fd = open_existing_dir(Path(photos), home.parent)
    try:
        deleted_fd = subdir(parent_fd, DELETED_DIRNAME, trashed.parent)
        try:
            os.rename(trashed.name, home.name, src_dir_fd=deleted_fd, dst_dir_fd=parent_fd)
        finally:
            os.close(deleted_fd)
        with contextlib.suppress(OSError):
            os.rmdir(DELETED_DIRNAME, dir_fd=parent_fd)  # only when it is empty now
    finally:
        os.close(parent_fd)


def remove_deleted_dir(path: str | Path, photos_root: str | Path) -> bool:
    """Permanently delete one expired .icloud2nas-deleted entry; False when it is already gone.

    The only rmtree of a whole library: the entry's parent must be a real (not symlinked)
    `.icloud2nas-deleted` folder that resolves inside the photos root.
    """
    target = Path(path)
    if not target.is_absolute() or ".." in target.parts or target.parent.name != DELETED_DIRNAME:
        raise ValueError(f"not a deleted-library folder: {str(path)!r}")
    parent = target.parent
    root = Path(photos_root)
    if parent.is_symlink():
        raise ValueError(f"{str(parent)!r} is a symlink; refusing to delete through it")
    if not parent.is_relative_to(root) or not parent.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"{str(path)!r} is outside the photos root")
    try:
        fd = open_existing_dir(root, parent)
    except ValueError:
        if not parent.exists():
            return False
        raise
    try:
        try:
            st = os.stat(target.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            os.unlink(target.name, dir_fd=fd)  # a planted link: drop it, never its target
            return True
        shutil.rmtree(target.name, dir_fd=fd)
    finally:
        os.close(fd)
    with contextlib.suppress(OSError, ValueError):
        gfd = open_existing_dir(root, parent.parent)
        try:
            os.rmdir(parent.name, dir_fd=gfd)  # only when it is empty now
        finally:
            os.close(gfd)
    return True



def _day_of(trash_fd: int, name: str) -> date | None:
    """The date of a real trash day folder `name` in the open trash folder (ISO-named, not a
    link, carrying the `.i2n-day` marker as a regular file); None for anything else."""
    try:
        day = date.fromisoformat(name)
    except ValueError:
        return None
    try:
        day_fd = subdir(trash_fd, name, Path(name))
    except (FileNotFoundError, ValueError):
        return None
    try:
        st = os.stat(DAY_MARKER, dir_fd=day_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    finally:
        os.close(day_fd)
    return day if stat.S_ISREG(st.st_mode) else None


def purge_expired(
    library_dir: str | Path,
    retention_days: int,
    today: date | None = None,
    *,
    photos: str | Path,
) -> list[Path]:
    """Delete this library's trash day-folders older than retention_days. 0 disables purging.

    ValueError when the library, a parent of it, its store or its trash is a symlink (the
    caller logs and skips the library); a library without a trash has nothing to purge.
    """
    if retention_days <= 0:
        return []
    lib = _lib(library_dir)
    root = trash_dir(lib)
    cutoff = (today or date.today()) - timedelta(days=retention_days)
    try:
        fd = _open_below(photos, lib, root)
    except FileNotFoundError:
        return []
    removed: list[Path] = []
    try:
        for name in sorted(os.listdir(fd)):  # day dirs are direct children; never descend
            day = _day_of(fd, name)
            if day is not None and day < cutoff:
                shutil.rmtree(name, dir_fd=fd)
                removed.append(root / name)
    finally:
        os.close(fd)
    return removed


@dataclass(frozen=True)
class TrashDay:
    day: date
    files: int
    bytes: int


@dataclass(frozen=True)
class TrashFile:
    rel: str
    size: int


def _files_below(fd: int) -> list[TrashFile]:
    """Regular files under the open folder fd (links never followed), sorted by rel."""
    out: list[TrashFile] = []
    for dirpath, _dirs, files, dir_fd in os.fwalk(".", dir_fd=fd, follow_symlinks=False):
        for name in files:
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(st.st_mode):
                out.append(TrashFile(posixpath.normpath(posixpath.join(dirpath, name)), st.st_size))
    return sorted(out, key=lambda f: f.rel)


def _day_files(day_fd: int) -> list[TrashFile]:
    return [f for f in _files_below(day_fd) if f.rel != DAY_MARKER]


def _open_trash_day(photos: str | Path, lib: Path, day: date) -> tuple[int, int]:
    """(trash folder fd, day folder fd) of one library's trash day; ValueError when there is
    no such day or a component is a symlink. The caller closes both."""
    missing = ValueError(f"no trash day {day.isoformat()} in {str(lib)!r}")
    try:
        trash_fd = _open_below(photos, lib, trash_dir(lib))
    except FileNotFoundError:
        raise missing from None
    try:
        if _day_of(trash_fd, day.isoformat()) is None:
            raise missing
        day_fd = subdir(trash_fd, day.isoformat(), trash_day_dir(lib, day))
    except BaseException:
        os.close(trash_fd)
        raise
    return trash_fd, day_fd


def list_days(library_dir: str | Path, *, photos: str | Path) -> list[TrashDay]:
    """Day folders of one library's trash, newest first; ValueError when a component of the
    way there is a symlink."""
    lib = _lib(library_dir)
    try:
        trash_fd = _open_below(photos, lib, trash_dir(lib))
    except FileNotFoundError:
        return []
    days: list[TrashDay] = []
    try:
        for name in os.listdir(trash_fd):
            day = _day_of(trash_fd, name)
            if day is None:
                continue
            try:
                day_fd = subdir(trash_fd, name, trash_dir(lib) / name)
            except (FileNotFoundError, ValueError):
                continue
            try:
                files = _day_files(day_fd)
            finally:
                os.close(day_fd)
            days.append(TrashDay(day, len(files), sum(f.size for f in files)))
    finally:
        os.close(trash_fd)
    return sorted(days, key=lambda d: d.day, reverse=True)


def list_day(library_dir: str | Path, day: date, *, photos: str | Path) -> list[TrashFile]:
    trash_fd, day_fd = _open_trash_day(photos, _lib(library_dir), day)
    try:
        return _day_files(day_fd)
    finally:
        os.close(day_fd)
        os.close(trash_fd)


def delete_day(library_dir: str | Path, day: date, *, photos: str | Path) -> int:
    """Permanently delete one trash day folder of this library; returns files removed."""
    trash_fd, day_fd = _open_trash_day(photos, _lib(library_dir), day)
    try:
        try:
            count = len(_day_files(day_fd))
        finally:
            os.close(day_fd)
        shutil.rmtree(day.isoformat(), dir_fd=trash_fd)
    finally:
        os.close(trash_fd)
    return count


def _check_dest(photos: str | Path, lib: Path, dest: Path) -> None:
    """ValueError when an existing folder on the way to dest is a symlink (or not a folder)."""
    try:
        fd = _open_below(photos, lib, dest.parent)
    except FileNotFoundError:
        return  # the rest is made at move time
    os.close(fd)


def keep_files(
    library_dir: str | Path,
    day: date,
    rels: list[str],
    *,
    photos: str | Path,
    on_create: OnCreate | None = None,
) -> list[Path]:
    """Rescue trash files into <library>/.icloud2nas/kept/<rel>; all-or-nothing validation.

    The library is reached from the photos root and both sides from it one O_NOFOLLOW
    component at a time (no symlink is ever followed); every file and destination is
    validated before anything moves, a name clash gets `~N` (`name~1.ext`, ...), and each move
    is link + unlink (never a copy, never an overwrite). Folders made under kept/ go to
    on_create (the API hands them to the owner).
    """
    lib = _lib(library_dir)
    day_dir = trash_day_dir(lib, day)
    kept_base = kept_dir(lib)
    fds: list[int] = []
    try:
        trash_fd, day_fd = _open_trash_day(photos, lib, day)
        fds += [trash_fd, day_fd]
        plan: list[tuple[str, int, str, Path]] = []
        seen: set[tuple[int, int]] = set()
        for rel in rels:
            pure = Path(rel)
            parts = pure.parts
            if pure.is_absolute() or ".." in parts or not parts or parts == (DAY_MARKER,):
                raise ValueError(f"invalid trash path: {rel!r}")
            try:
                parent_fd = descend(day_fd, day_dir, parts[:-1])
            except FileNotFoundError:
                raise ValueError(f"invalid or duplicate trash path: {rel!r}") from None
            fds.append(parent_fd)
            try:
                st = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                raise ValueError(f"invalid or duplicate trash path: {rel!r}") from None
            if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) in seen:
                raise ValueError(f"invalid or duplicate trash path: {rel!r}")
            seen.add((st.st_dev, st.st_ino))
            dest = kept_base / pure
            _check_dest(photos, lib, dest)
            plan.append((rel, parent_fd, parts[-1], dest))
        moved: list[Path] = []
        for rel, parent_fd, name, dest in plan:
            try:
                dest_fd = _open_below(photos, lib, dest.parent, create=True, on_create=on_create)
            except FileNotFoundError:
                raise ValueError(f"cannot keep {rel!r}: the library folder is gone") from None
            try:
                err, used = _link_move(parent_fd, name, dest_fd, dest.name)
            finally:
                os.close(dest_fd)
            if err is not None:
                raise ValueError(f"cannot keep {rel!r}: {err}")
            moved.append(dest.with_name(used))
        return moved
    finally:
        for fd in fds:
            os.close(fd)


def trash_bytes(library_dirs: Iterable[str | Path], *, photos: str | Path) -> int:
    """Bytes held in the trash of every given library (a symlinked one counts as empty)."""
    total = 0
    for library_dir in library_dirs:
        lib = _lib(library_dir)
        try:
            fd = _open_below(photos, lib, trash_dir(lib))
        except (FileNotFoundError, ValueError):
            continue
        try:
            total += sum(f.size for f in _files_below(fd))
        finally:
            os.close(fd)
    return total


# -- Plan 2 layout migration ------------------------------------------------------------------


def _same_device(src: Path, dest: Path) -> bool:
    ancestor = dest.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    return os.stat(src).st_dev == os.stat(ancestor).st_dev


def _prune_empty(root: Path, stop: Path) -> None:
    """rmdir empty directories from root's subtree upwards, then root's ancestors up to stop."""
    if root.is_dir():
        for dirpath, _dirs, _files in os.walk(root, topdown=False):
            with contextlib.suppress(OSError):
                os.rmdir(dirpath)
    here = root.parent
    while here != stop and here.is_relative_to(stop):
        try:
            here.rmdir()
        except OSError:
            break
        here = here.parent


def _merge_tree(src: Path, dest: Path, lib: Path, photos: Path, *, marker: bool) -> str | None:
    """Rename src to dest; when dest exists, move file by file (`~N` on a name clash). Returns
    an error. dest lies in lib's store, reached with O_NOFOLLOW (open_dir); files move by
    link + unlink, so nothing is ever overwritten. Never raises OSError
    or ValueError: a collision such as a file where src has a folder is reported too.
    """
    try:
        return _merge_tree_or_raise(src, dest, lib, photos, marker=marker)
    except OSError as exc:
        return exc.strerror or str(exc)
    except ValueError as exc:
        return str(exc)


def _merge_tree_or_raise(
    src: Path, dest: Path, lib: Path, photos: Path, *, marker: bool
) -> str | None:
    if not _same_device(src, dest):
        return "cross-device"
    try:
        dest_fd = _open_below(photos, lib, dest)
    except FileNotFoundError:
        parent_fd = _open_below(photos, lib, dest.parent, create=True)
        try:
            os.rename(src, dest.name, dst_dir_fd=parent_fd)  # rename only, never a copy
        except OSError as exc:
            return "cross-device" if exc.errno == errno.EXDEV else (exc.strerror or str(exc))
        finally:
            os.close(parent_fd)
        return None
    try:
        for path in sorted(src.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            rel = path.relative_to(src)
            if marker and rel.as_posix() == DAY_MARKER:
                continue  # dest keeps (or gets) its own marker
            parent_fd = descend(dest_fd, dest, rel.parts[:-1], create=True)
            try:
                err, _used = _link_move(None, str(path), parent_fd, path.name)
            finally:
                os.close(parent_fd)
            if err is not None:
                return err
        if marker:
            flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
            flags |= getattr(os, "O_CLOEXEC", 0)
            os.close(os.open(DAY_MARKER, flags, 0o644, dir_fd=dest_fd))
            (src / DAY_MARKER).unlink(missing_ok=True)  # our marker, not a library file
    finally:
        os.close(dest_fd)
    return None


def _legacy_trash_rels(root: Path) -> list[str]:
    """Rels under the legacy trash root that directly contain a day dir."""
    rels: list[str] = []
    if not root.is_dir():
        return rels
    for dirpath, dirnames, _files in os.walk(root):
        here = Path(dirpath)
        if any(_is_day_dir(here / n) for n in dirnames):
            rels.append(here.relative_to(root).as_posix())
        dirnames[:] = [n for n in dirnames if not _is_day_dir(here / n)]
    return sorted(rels)


def migrate_legacy(photos_root: str | Path, library_rels: Iterable[str]) -> list[str]:
    """Move Plan 2's root-level trash and kept folders into each known library (rename only).

    `/photos/.trash/<rel>/<day>` merges into `<lib>/.icloud2nas/trash/<day>`, `/photos/kept/<rel>`
    into `<lib>/.icloud2nas/kept`; file collisions get a `~N` suffix. Unknown rels, missing
    library folders and cross-device trees are left in place. Returns log lines; idempotent.
    """
    photos = Path(photos_root)
    legacy_trash = photos / LEGACY_TRASH_DIRNAME
    legacy_kept = photos / LEGACY_KEPT_DIRNAME
    lines: list[str] = []
    for rel in sorted(set(library_rels)):
        old_trash, old_kept = legacy_trash / rel, legacy_kept / rel
        if not old_trash.is_dir() and not old_kept.is_dir():
            continue
        lib = photos / rel
        if not lib.is_dir():
            lines.append(f"warning: library folder {rel!r} is missing; legacy trash left in place")
            continue
        if old_trash.is_dir():
            for child in sorted(old_trash.iterdir()):
                if child.is_symlink() or not _is_day_dir(child):
                    continue
                dest = trash_day_dir(lib, date.fromisoformat(child.name))
                err = _merge_tree(child, dest, lib, photos, marker=True)
                if err is None:
                    lines.append(f"moved legacy trash {child} -> {dest}")
                else:
                    lines.append(f"warning: legacy trash {child} left in place: {err}")
            _prune_empty(old_trash, legacy_trash)
        if old_kept.is_dir():
            dest = kept_dir(lib)
            err = _merge_tree(old_kept, dest, lib, photos, marker=False)
            if err is None:
                lines.append(f"moved legacy kept {old_kept} -> {dest}")
            else:
                lines.append(f"warning: legacy kept {old_kept} left in place: {err}")
            _prune_empty(old_kept, legacy_kept)
    for left in _legacy_trash_rels(legacy_trash):
        lines.append(f"warning: legacy trash of unknown library {left!r} left in {legacy_trash}")
    if legacy_kept.is_dir():
        for child in sorted(legacy_kept.iterdir()):
            lines.append(f"warning: legacy kept {child.name!r} left in {legacy_kept}")
    for root in (legacy_trash, legacy_kept):
        with contextlib.suppress(OSError):
            root.rmdir()  # only when nothing is left
    return lines
