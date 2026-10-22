"""Shared validation for finite numeric inputs; booleans are not measurements."""
import math


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive_number(value):
    return finite_number(value) and value > 0


def nonnegative_number(value):
    return finite_number(value) and value >= 0
