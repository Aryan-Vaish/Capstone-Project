from module import withdraw

def test_withdraw_success():
    assert withdraw(100, 40) == 60

def test_withdraw_insufficient():
    try:
        withdraw(50, 100)
        assert False
    except ValueError:
        pass
