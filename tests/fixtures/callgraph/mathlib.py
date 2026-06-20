"""Fixture with a KNOWN call structure, used to validate callers/callees.

Call edges (in-repo only):
    compute -> add, square
    square  -> mul
    add     -> (none)
    mul     -> (none)
    Helper.run -> compute        (method body references a module function)
"""


def add(a, b):
    return a + b


def mul(a, b):
    return a * b


def square(x):
    return mul(x, x)


def compute(n):
    s = add(n, 1)
    return square(s)


class Helper:
    def run(self, n):
        return compute(n)
