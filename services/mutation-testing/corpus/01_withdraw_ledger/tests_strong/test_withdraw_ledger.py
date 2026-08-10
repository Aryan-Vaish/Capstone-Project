"""Oracle suite: intended to kill essentially every non-equivalent mutant.

Paired with tests_weak/ to produce ground-truth gap labels -- a mutant that
survives the weak suite but dies here is a REAL missing test, which is a label
mutation-testing research normally has no access to.
"""
import pytest

from withdraw_ledger import withdraw


def test_withdraw_success():
    assert withdraw(100, 40) == 60


def test_withdraw_exact_balance_is_allowed():
    # kills `>` -> `>=` : the boundary the weak suite never touches
    assert withdraw(100, 100) == 0


def test_withdraw_one_cent_over_raises():
    with pytest.raises(ValueError):
        withdraw(100, 100.01)


def test_withdraw_insufficient_message_is_exact():
    # kills all three string-literal mutations, and ValueError(None)
    with pytest.raises(ValueError) as exc:
        withdraw(50, 100)
    assert str(exc.value) == "Insufficient funds"


def test_withdraw_subtracts_rather_than_adds():
    # kills `-` -> `+`
    assert withdraw(10, 3) == 7
    assert withdraw(0, 0) == 0
