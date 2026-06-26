"""Contract round-trip tests for the portable one-file backup models (M12 portable backup, P1)."""

import json
from datetime import UTC, datetime

from doktok_contracts.schemas import (
    BackupExportInfo,
    BackupManifest,
    BackupManifestMember,
)


def test_manifest_member_roundtrip() -> None:
    member = BackupManifestMember(name="db.dump", size=1234, sha256="a" * 64)
    again = BackupManifestMember.model_validate(json.loads(member.model_dump_json()))
    assert again == member


def test_manifest_roundtrip_carries_all_fields() -> None:
    created = datetime.now(UTC)
    manifest = BackupManifest(
        schema_version=1,
        created_at=created,
        app_version="0.2.0",
        pg_version="17.2",
        members=[
            BackupManifestMember(name="db.dump", size=10, sha256="b" * 64),
            BackupManifestMember(name="files/a.pdf", size=20, sha256="c" * 64),
        ],
        manifest_hmac="d" * 64,
        secrets_key_fingerprint="e" * 64,
    )
    again = BackupManifest.model_validate(json.loads(manifest.model_dump_json()))
    assert again == manifest
    assert again.schema_version == 1
    assert {m.name for m in again.members} == {"db.dump", "files/a.pdf"}
    assert again.secrets_key_fingerprint == "e" * 64


def test_manifest_defaults() -> None:
    manifest = BackupManifest(created_at=datetime.now(UTC), app_version="0.2.0", pg_version="17")
    assert manifest.schema_version == 1
    assert manifest.members == []
    assert manifest.manifest_hmac == ""
    assert manifest.secrets_key_fingerprint == ""


def test_export_info_roundtrip_ready() -> None:
    created = datetime.now(UTC)
    info = BackupExportInfo(
        export_id="abc123",
        status="ready",
        created_at=created,
        size_bytes=4096,
        app_version="0.2.0",
        pg_version="17.2",
        member_count=3,
    )
    again = BackupExportInfo.model_validate(json.loads(info.model_dump_json()))
    assert again == info
    assert again.status == "ready"
    assert again.error == ""


def test_export_info_defaults_building() -> None:
    info = BackupExportInfo(export_id="x", status="building")
    assert info.created_at is None
    assert info.size_bytes is None
    assert info.member_count == 0
    assert info.error == ""
