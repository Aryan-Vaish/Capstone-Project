def withdraw(balance: float, amount: float) -> float:
    if amount > balance:
        raise ValueError("Insufficient funds")
    return balance - amount
