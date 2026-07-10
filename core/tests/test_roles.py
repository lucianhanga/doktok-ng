from doktok_core.security.roles import Role, parse_role, role_at_least


def test_role_ordering() -> None:
    assert role_at_least(Role.ADMIN, Role.VIEWER)
    assert role_at_least(Role.ADMIN, Role.EDITOR)
    assert role_at_least(Role.ADMIN, Role.ADMIN)
    assert role_at_least(Role.EDITOR, Role.VIEWER)
    assert role_at_least(Role.EDITOR, Role.EDITOR)
    assert role_at_least(Role.VIEWER, Role.VIEWER)


def test_role_ordering_rejects_insufficient() -> None:
    assert not role_at_least(Role.VIEWER, Role.EDITOR)
    assert not role_at_least(Role.VIEWER, Role.ADMIN)
    assert not role_at_least(Role.EDITOR, Role.ADMIN)


def test_parse_role_known_values() -> None:
    assert parse_role("viewer") is Role.VIEWER
    assert parse_role("editor") is Role.EDITOR
    assert parse_role("admin") is Role.ADMIN


def test_parse_role_unknown_fails_closed_to_viewer() -> None:
    assert parse_role(None) is Role.VIEWER
    assert parse_role("") is Role.VIEWER
    assert parse_role("superuser") is Role.VIEWER
