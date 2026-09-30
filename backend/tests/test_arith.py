"""Arithmetic and formatting rules, tested apart from any compiler structure.

These are the semantics the interpreter and the constant folder both depend on. If a rule
changes here, both change together, so these tests are the contract that keeps them
honest.
"""

from __future__ import annotations

import pytest

from opticode.semantics import (
    INT_MAX,
    INT_MIN,
    DivisionByZero,
    arith_binary,
    arith_unary,
    format_float,
    render_format,
    truncate_to_int,
    wrap_int,
)


class TestWraparound:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, 0),
            (INT_MAX, INT_MAX),
            (INT_MAX + 1, INT_MIN),
            (INT_MIN - 1, INT_MAX),
            (2**31, INT_MIN),
            (-(2**31) - 5, INT_MAX - 4),
            (2**40 + 7, 7),
        ],
    )
    def test_wrap_int(self, value: int, expected: int):
        assert wrap_int(value) == expected

    def test_addition_overflows_like_c(self):
        assert arith_binary("+", INT_MAX, 1) == INT_MIN

    def test_subtraction_overflows_like_c(self):
        assert arith_binary("-", INT_MIN, 1) == INT_MAX

    def test_multiplication_overflows_like_c(self):
        assert arith_binary("*", 65536, 65536) == 0

    def test_negating_int_min_stays_int_min(self):
        assert arith_unary("-", INT_MIN) == INT_MIN

    def test_results_never_escape_the_32_bit_range(self):
        for left, right in ((INT_MAX, INT_MAX), (INT_MIN, 3), (-1, INT_MIN)):
            for op in ("+", "-", "*"):
                assert INT_MIN <= arith_binary(op, left, right) <= INT_MAX


class TestIntegerDivision:
    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            (7, 2, 3),
            (-7, 2, -3),
            (7, -2, -3),
            (-7, -2, 3),
            (1, 2, 0),
            (-1, 2, 0),
        ],
    )
    def test_division_truncates_toward_zero(self, left: int, right: int, expected: int):
        assert arith_binary("/", left, right) == expected

    @pytest.mark.parametrize(
        ("left", "right", "expected"),
        [
            (7, 2, 1),
            (-7, 2, -1),
            (7, -2, 1),
            (-7, -2, -1),
            (0, 5, 0),
        ],
    )
    def test_modulo_sign_follows_the_dividend(self, left: int, right: int, expected: int):
        assert arith_binary("%", left, right) == expected

    @pytest.mark.parametrize("op", ["/", "%"])
    def test_division_by_zero_traps(self, op: str):
        with pytest.raises(DivisionByZero):
            arith_binary(op, 5, 0)

    def test_truncation_and_modulo_are_consistent(self):
        """A C-style identity: ``a == (a / b) * b + a % b``."""
        for left, right in ((7, 3), (-7, 3), (7, -3), (-7, -3), (22, 7)):
            quotient = arith_binary("/", left, right)
            remainder = arith_binary("%", left, right)
            assert quotient * right + remainder == left


class TestFloatArithmetic:
    def test_float_operand_promotes_the_result(self):
        result = arith_binary("+", 1, 2.5)
        assert isinstance(result, float)
        assert result == 3.5

    def test_int_only_when_both_are_int(self):
        assert isinstance(arith_binary("+", 1, 2), int)

    def test_modulo_is_rejected_for_floats(self):
        with pytest.raises(ValueError):
            arith_binary("%", 1.5, 2.0)

    def test_float_division_by_zero_traps(self):
        """IEEE would give inf, so this is rejected deliberately rather than inherited."""
        with pytest.raises(DivisionByZero):
            arith_binary("/", 1.0, 0.0)

    def test_float_division_by_negative_zero_also_traps(self):
        with pytest.raises(DivisionByZero):
            arith_binary("/", 1.0, -0.0)

    def test_float_overflow_follows_ieee(self):
        assert arith_binary("*", 1e308, 10) == float("inf")


class TestComparisons:
    @pytest.mark.parametrize(
        ("op", "expected"),
        [("==", 1), ("!=", 0)],
    )
    def test_equality(self, op: str, expected: int):
        assert arith_binary(op, 3, 3) == expected

    @pytest.mark.parametrize(
        ("op", "left", "right", "expected"),
        [("<", 1, 2, 1), ("<", 2, 1, 0), (">=", 2, 2, 1), ("<=", 1, 2, 1)],
    )
    def test_ordering_yields_int(self, op: str, left: int, right: int, expected: int):
        result = arith_binary(op, left, right)
        assert result == expected
        assert isinstance(result, int)

    def test_float_comparison_still_yields_int(self):
        assert arith_binary("<", 1.5, 2.5) == 1
        assert isinstance(arith_binary("<", 1.5, 2.5), int)

    def test_zero_is_false_and_nonzero_is_true(self):
        assert arith_binary("&&", 0, 1) == 0
        assert arith_binary("&&", 1, 5) == 1
        assert arith_binary("||", 0, 0) == 0
        assert arith_binary("||", 0, 7) == 1

    def test_negative_zero_is_still_zero(self):
        assert arith_binary("&&", -0.0, 1) == 0


class TestUnary:
    @pytest.mark.parametrize(("op", "value", "expected"), [("-", 5, -5), ("+", 5, 5)])
    def test_identity_and_negation(self, op: str, value: int, expected: int):
        assert arith_unary(op, value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"), [(0, 1), (1, 0), (-1, 0), (0.0, 1), (0.5, 0)]
    )
    def test_logical_not(self, value: float | int, expected: int):
        assert arith_unary("!", value) == expected

    def test_unknown_operator_is_rejected(self):
        with pytest.raises(ValueError):
            arith_unary("~", 1)


class TestConversions:
    def test_truncate_to_int_truncates_toward_zero(self):
        assert truncate_to_int(1.9) == 1
        assert truncate_to_int(-1.9) == -1

    def test_truncate_wraps_out_of_range_values(self):
        assert truncate_to_int(1e18) == wrap_int(int(1e18))

    def test_truncate_maps_nan_to_zero(self):
        assert truncate_to_int(float("nan")) == 0


class TestFormatRendering:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0.0, "0.000000"),
            (2.0, "2.000000"),
            (1.5, "1.500000"),
            (-1.5, "-1.500000"),
            (0.1, "0.100000"),
            (float("inf"), "inf"),
        ],
    )
    def test_float_formatting_matches_c(self, value: float, expected: str):
        assert format_float(value) == expected

    def test_render_format_consumes_values_in_order(self):
        assert render_format("%d %f\n", [3, 1.5]) == "3 1.500000\n"

    def test_percent_percent_writes_a_literal_percent(self):
        assert render_format("100%%\n", []) == "100%\n"

    def test_int_specifier_truncates_a_float(self):
        assert render_format("%d", [1.9]) == "1"

    def test_float_specifier_widens_an_int(self):
        assert render_format("%f", [2]) == "2.000000"

    def test_mismatched_arity_raises(self):
        with pytest.raises(ValueError):
            render_format("%d %d", [1])
        with pytest.raises(ValueError):
            render_format("%d", [1, 2])

    def test_unsupported_specifier_raises(self):
        with pytest.raises(ValueError):
            render_format("%s", ["x"])


def test_unknown_binary_operator_is_rejected():
    with pytest.raises(ValueError):
        arith_binary("**", 2, 3)
