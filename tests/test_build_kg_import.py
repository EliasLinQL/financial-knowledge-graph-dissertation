from __future__ import annotations

import unittest

from src.build_kg_import import integer_text, rounded_integer_text


class NumericTextTests(unittest.TestCase):
    def test_market_cap_decimal_is_rounded_to_nearest_dollar(self) -> None:
        self.assertEqual(
            rounded_integer_text("504506813533.46"),
            "504506813533",
        )
        self.assertEqual(
            rounded_integer_text("504506813533.50"),
            "504506813534",
        )

    def test_market_cap_integer_and_blank_remain_compatible(self) -> None:
        self.assertEqual(rounded_integer_text("5136204579214.0"), "5136204579214")
        self.assertEqual(rounded_integer_text(""), "")

    def test_non_finite_or_invalid_market_cap_is_rejected(self) -> None:
        for value in ("not-a-number", "NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    rounded_integer_text(value)

    def test_strict_integer_fields_still_reject_fractional_values(self) -> None:
        with self.assertRaises(ValueError):
            integer_text("1.5")


if __name__ == "__main__":
    unittest.main()
