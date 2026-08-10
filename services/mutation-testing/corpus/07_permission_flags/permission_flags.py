"""Bitmask permissions.

The only module in the corpus exercising bitwise operators and shifts. mutmut
mutates all of them (AND to OR, OR to AND, XOR to AND, left-shift to
right-shift), and the original three-module corpus contained no instance of any
of them -- so those taxonomy slots had no training signal at all.
"""

READ = 1 << 0
WRITE = 1 << 1
EXECUTE = 1 << 2
ADMIN = 1 << 3

ALL_FLAGS = READ | WRITE | EXECUTE | ADMIN

FLAG_NAMES = (("read", READ), ("write", WRITE), ("execute", EXECUTE), ("admin", ADMIN))


def has_permission(mask: int, flag: int) -> bool:
    return mask & flag != 0


def grant(mask: int, flag: int) -> int:
    return mask | flag


def revoke(mask: int, flag: int) -> int:
    return mask & ~flag


def toggle(mask: int, flag: int) -> int:
    return mask ^ flag


def is_admin(mask: int) -> bool:
    return has_permission(mask, ADMIN)


def describe(mask: int) -> list:
    names = []
    for name, flag in FLAG_NAMES:
        if has_permission(mask, flag):
            names.append(name)
    return names
