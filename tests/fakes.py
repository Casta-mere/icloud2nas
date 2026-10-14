"""Synthetic CloudKit records that build real pyicloud_ipd objects, plus tiny stand-ins."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pyicloud_ipd.services.photos import PhotoAsset

HEIC = "public.heic"
JPEG = "public.jpeg"
MOV = "com.apple.quicktime-movie"


def make_asset(
    record_name: str,
    filename: str | None,
    taken: datetime,
    *,
    size: int = 1000,
    item_type: str = HEIC,
    live_photo_size: int | None = None,
) -> PhotoAsset:
    fields: dict[str, Any] = {
        "itemType": {"type": "STRING", "value": item_type},
        "resOriginalRes": {
            "type": "ASSETID",
            "value": {
                "size": size,
                "downloadURL": f"https://example.invalid/{record_name}",
                "fileChecksum": "chk",
            },
        },
        "resOriginalFileType": {"type": "STRING", "value": item_type},
    }
    if filename is not None:
        fields["filenameEnc"] = {
            "type": "ENCRYPTED_BYTES",
            "value": base64.b64encode(filename.encode("utf-8")).decode("ascii"),
        }
    if live_photo_size is not None:
        fields["resOriginalVidComplRes"] = {
            "type": "ASSETID",
            "value": {
                "size": live_photo_size,
                "downloadURL": f"https://example.invalid/{record_name}.mov",
                "fileChecksum": "chk2",
            },
        }
        fields["resOriginalVidComplFileType"] = {"type": "STRING", "value": MOV}
    master = {"recordName": record_name, "recordType": "CPLMaster", "fields": fields}
    asset = {
        "recordName": f"{record_name}-asset",
        "recordType": "CPLAsset",
        "fields": {
            "assetDate": {"type": "TIMESTAMP", "value": int(taken.timestamp() * 1000)},
            "masterRef": {"value": {"recordName": record_name}},
        },
    }
    return PhotoAsset(master, asset)


def id7(record_name: str) -> str:
    return base64.b64encode(record_name.encode("utf-8")).decode("ascii")[:7]


@dataclass
class FakeLibrary:
    all: list[PhotoAsset] = field(default_factory=list)
    recently_deleted: list[PhotoAsset] = field(default_factory=list)


@dataclass
class FakePhotos(FakeLibrary):
    private_libraries: dict[str, FakeLibrary] = field(default_factory=dict)
    shared_libraries: dict[str, FakeLibrary] = field(default_factory=dict)


@dataclass
class FakeICloud:
    photos: FakePhotos = field(default_factory=FakePhotos)
