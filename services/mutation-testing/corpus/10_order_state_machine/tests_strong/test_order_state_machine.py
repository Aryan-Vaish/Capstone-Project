"""Oracle suite for order_state_machine."""
import pytest

from order_state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    apply_events,
    can_transition,
    is_terminal,
    reachable_states,
    transition,
)


@pytest.mark.parametrize("state", sorted(TRANSITIONS))
def test_is_terminal_matches_the_declared_set(state):
    assert is_terminal(state) is (state in TERMINAL_STATES)


def test_is_terminal_unknown_state():
    # kills `in` -> `not in`
    assert is_terminal("nonexistent") is False


def test_can_transition_every_declared_edge():
    for current, targets in TRANSITIONS.items():
        for target in targets:
            assert can_transition(current, target) is True


def test_can_transition_rejects_undeclared_edges():
    assert can_transition("new", "shipped") is False
    assert can_transition("delivered", "paid") is False


def test_can_transition_unknown_current_state():
    # kills `not in` -> `in` on the guard
    assert can_transition("nonexistent", "paid") is False


def test_transition_returns_target():
    assert transition("new", "paid") == "paid"
    assert transition("paid", "shipped") == "shipped"


def test_transition_requires_a_current_state():
    # kills `is None` -> `is not None`
    with pytest.raises(ValueError) as exc:
        transition(None, "paid")
    assert str(exc.value) == "Current state is required"


def test_transition_illegal_raises_with_message():
    with pytest.raises(ValueError) as exc:
        transition("new", "delivered")
    assert str(exc.value) == "Illegal transition"


def test_apply_events_follows_a_valid_chain():
    assert apply_events("new", ["paid", "shipped"]) == "shipped"


def test_apply_events_skips_illegal_events():
    # kills `continue` -> `break`: a break would stop at the bad event
    assert apply_events("new", ["delivered", "paid"]) == "paid"


def test_apply_events_stops_at_a_terminal_state():
    # kills `break` -> `return`/removal: without it, later events would apply
    assert apply_events("new", ["cancelled", "paid"]) == "cancelled"


def test_apply_events_with_no_events_returns_start():
    assert apply_events("new", []) == "new"


def test_reachable_states_depth_one():
    assert reachable_states("new", depth=1) == {"new", "paid", "cancelled"}


def test_reachable_states_default_depth_is_two():
    assert reachable_states("new") == {"new", "paid", "cancelled", "shipped", "refunded"}


def test_reachable_states_does_not_revisit():
    # kills `not in seen` -> `in seen`
    result = reachable_states("returned", depth=3)
    assert result == {"returned", "refunded"}


def test_reachable_states_terminal_start():
    assert reachable_states("delivered") == {"delivered"}
