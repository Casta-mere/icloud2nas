import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from icloud2nas.engine.job import FilePolicy
from icloud2nas.engine.paths import expected_paths, filename_builder, walk_library
from tests.fakes import HEIC, MOV, id7, make_asset

SH = ZoneInfo("Asia/Shanghai")
TAKEN = datetime(2024, 5, 3, 10, 0, tzinfo=SH)


def test_id7_policy_year_month_layout(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("AbC123xyz", "IMG_0421.HEIC", TAKEN)
    assert expected_paths(asset, str(lib), FilePolicy()) == {
        os.path.join(str(lib), "2024", "05", f"IMG_0421_{id7('AbC123xyz')}.HEIC")
    }


def test_live_photo_adds_same_named_mov(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("LIVE1", "IMG_0001.HEIC", TAKEN, live_photo_size=55)
    paths = expected_paths(asset, str(lib), FilePolicy())
    stem = f"IMG_0001_{id7('LIVE1')}"
    assert paths == {
        os.path.join(str(lib), "2024", "05", f"{stem}.HEIC"),
        os.path.join(str(lib), "2024", "05", f"{stem}.MOV"),
    }


def test_live_photo_suffix_policy(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("LIVE2", "IMG_0002.HEIC", TAKEN, live_photo_size=55)
    policy = FilePolicy(live_photo_mov_filename_policy="suffix")
    names = {os.path.basename(p) for p in expected_paths(asset, str(lib), policy)}
    assert names == {f"IMG_0002_{id7('LIVE2')}.HEIC", f"IMG_0002_{id7('LIVE2')}_HEVC.MOV"}


def test_video_asset_gets_mov_extension(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("VID1", "clip.mp4", TAKEN, item_type=MOV)
    names = {os.path.basename(p) for p in expected_paths(asset, str(lib), FilePolicy())}
    assert names == {f"clip_{id7('VID1')}.MOV"}


def test_two_assets_same_name_same_month_differ(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    a = expected_paths(make_asset("A1", "IMG_1.HEIC", TAKEN), str(lib), FilePolicy())
    b = expected_paths(make_asset("B2", "IMG_1.HEIC", TAKEN), str(lib), FilePolicy())
    assert a.isdisjoint(b)


def test_name_size_policy_includes_dedup_variant(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("A1", "IMG_1.HEIC", TAKEN, size=777)
    policy = FilePolicy(file_match_policy="name-size-dedup-with-suffix")
    names = {os.path.basename(p) for p in expected_paths(asset, str(lib), policy)}
    assert names == {"IMG_1.HEIC", "IMG_1-777.HEIC"}


def test_missing_filename_uses_fingerprint_fallback(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("Ab/c+D=1234567890", None, TAKEN)
    names = {os.path.basename(p) for p in expected_paths(asset, str(lib), FilePolicy())}
    # fingerprint = first 12 chars of the id with non-alphanumerics replaced by "_"
    assert names == {f"Ab_c_D_12345_{id7('Ab/c+D=1234567890')}.HEIC"}


def test_unicode_kept_and_reserved_chars_replaced(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("U1", "旅行:夏天*1.HEIC", TAKEN)
    names = {os.path.basename(p) for p in expected_paths(asset, str(lib), FilePolicy())}
    assert names == {f"旅行_夏天_1_{id7('U1')}.HEIC"}
    ascii_only = FilePolicy(keep_unicode_in_filenames=False)
    names = {os.path.basename(p) for p in expected_paths(asset, str(lib), ascii_only)}
    assert names == {f"__1_{id7('U1')}.HEIC"}


def test_folder_structure_none_flattens(photos_tree: tuple[Path, Path]) -> None:
    _, lib = photos_tree
    asset = make_asset("F1", "x.HEIC", TAKEN)
    paths = expected_paths(asset, str(lib), FilePolicy(folder_structure="none"))
    assert paths == {os.path.join(str(lib), f"x_{id7('F1')}.HEIC")}


def test_filename_builder_matches_icloudpd_policy(photos_tree: tuple[Path, Path]) -> None:
    asset = make_asset("Q9", "IMG_9.JPG", TAKEN, item_type=HEIC)
    assert filename_builder(FilePolicy())(asset) == f"IMG_9_{id7('Q9')}.JPG"


def test_walk_library_skips_part_and_dotfiles_and_missing_dir(
    photos_tree: tuple[Path, Path],
) -> None:
    _, lib = photos_tree
    (lib / "2024" / "05").mkdir(parents=True)
    keep = lib / "2024" / "05" / "IMG_1.HEIC"
    keep.write_bytes(b"x")
    (lib / "2024" / "05" / "abcd1234.part").write_bytes(b"partial")
    (lib / "2024" / "05" / ".DS_Store").write_bytes(b"meta")
    (lib / ".hidden").mkdir()
    (lib / ".hidden" / "f.HEIC").write_bytes(b"x")
    assert walk_library(str(lib)) == {os.path.normpath(str(keep))}
    assert walk_library(str(lib / "does-not-exist")) == set()
