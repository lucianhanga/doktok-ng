"""Portable one-file backup domain logic (M12 portable backup, Phase 1: export only).

Builds a single, self-contained, encrypted ``.tgz`` of the whole system (custom-format pg_dump +
the files_root tree + a signed manifest) that the operator can download. It is COMPLEMENTARY to the
restic/pgBackRest DRP, not a replacement. Restore (upload/wipe/import) is a separate later phase.

This package depends only on the stdlib + subprocesses (pg_dump/tar/gzip/openssl) and the filesystem
- never on an infrastructure adapter - so it stays inside the core layer (import-linter).
"""
