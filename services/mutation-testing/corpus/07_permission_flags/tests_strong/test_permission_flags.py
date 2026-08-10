"""Oracle suite for permission_flags."""
from permission_flags import (
    ADMIN,
    ALL_FLAGS,
    EXECUTE,
    READ,
    WRITE,
    describe,
    grant,
    has_permission,
    is_admin,
    revoke,
    toggle,
)


def test_flag_values_are_distinct_powers_of_two():
    # kills the left-shift -> right-shift mutations on every constant
    assert (READ, WRITE, EXECUTE, ADMIN) == (1, 2, 4, 8)
    assert ALL_FLAGS == 15


def test_has_permission_distinguishes_and_from_or():
    # under AND -> OR this would wrongly report True
    assert has_permission(READ, WRITE) is False
    assert has_permission(0, READ) is False
    assert has_permission(READ | WRITE, WRITE) is True


def test_has_permission_on_combined_mask():
    mask = READ | EXECUTE
    assert has_permission(mask, READ) is True
    assert has_permission(mask, EXECUTE) is True
    assert has_permission(mask, WRITE) is False
    assert has_permission(mask, ADMIN) is False


def test_grant_is_additive_and_idempotent():
    assert grant(READ, WRITE) == READ | WRITE
    assert grant(READ, READ) == READ
    assert grant(0, ADMIN) == ADMIN


def test_revoke_removes_only_the_named_flag():
    mask = READ | WRITE | EXECUTE
    assert revoke(mask, WRITE) == READ | EXECUTE
    assert has_permission(revoke(mask, WRITE), WRITE) is False
    assert has_permission(revoke(mask, WRITE), READ) is True


def test_revoke_of_absent_flag_is_a_noop():
    assert revoke(READ, WRITE) == READ


def test_toggle_flips_both_directions():
    # kills XOR -> AND
    assert toggle(READ, WRITE) == READ | WRITE
    assert toggle(READ | WRITE, WRITE) == READ


def test_is_admin_both_ways():
    assert is_admin(ADMIN) is True
    assert is_admin(READ | WRITE | EXECUTE) is False


def test_describe_full_and_empty():
    assert describe(ALL_FLAGS) == ["read", "write", "execute", "admin"]
    assert describe(0) == []


def test_describe_preserves_order_and_subset():
    assert describe(READ | ADMIN) == ["read", "admin"]
    assert describe(WRITE) == ["write"]
