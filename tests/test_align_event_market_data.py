import unittest

import pandas as pd

from src.align_event_market_data import calculate_return_window


class CalculateReturnWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dates = pd.date_range("2026-01-01", periods=10, freq="B")
        self.market = pd.DataFrame(
            {"Close": [100.0 + index for index in range(10)]},
            index=self.dates,
        )

    def test_pre_event_window_ends_at_the_shared_event_boundary(self) -> None:
        one_day = calculate_return_window(self.market, self.dates, 5, -1)
        three_day = calculate_return_window(self.market, self.dates, 5, -3)

        self.assertIsNotNone(one_day)
        self.assertIsNotNone(three_day)
        assert one_day is not None and three_day is not None
        self.assertEqual(one_day["baseline_date"], self.dates[3])
        self.assertEqual(one_day["window_end_date"], self.dates[4])
        self.assertAlmostEqual(one_day["cumulative_return"], 104.0 / 103.0 - 1.0)
        self.assertEqual(three_day["baseline_date"], self.dates[1])
        self.assertEqual(three_day["window_end_date"], self.dates[4])
        self.assertAlmostEqual(
            three_day["cumulative_return"], 104.0 / 101.0 - 1.0
        )

    def test_post_event_window_starts_at_the_same_event_boundary(self) -> None:
        one_day = calculate_return_window(self.market, self.dates, 5, 1)
        three_day = calculate_return_window(self.market, self.dates, 5, 3)

        self.assertIsNotNone(one_day)
        self.assertIsNotNone(three_day)
        assert one_day is not None and three_day is not None
        self.assertEqual(one_day["baseline_date"], self.dates[4])
        self.assertEqual(one_day["window_end_date"], self.dates[5])
        self.assertAlmostEqual(one_day["cumulative_return"], 105.0 / 104.0 - 1.0)
        self.assertEqual(three_day["baseline_date"], self.dates[4])
        self.assertEqual(three_day["window_end_date"], self.dates[7])
        self.assertAlmostEqual(
            three_day["cumulative_return"], 107.0 / 104.0 - 1.0
        )

    def test_incomplete_and_zero_windows_are_handled_explicitly(self) -> None:
        self.assertIsNone(calculate_return_window(self.market, self.dates, 5, -7))
        self.assertIsNone(calculate_return_window(self.market, self.dates, 5, 7))
        with self.assertRaises(ValueError):
            calculate_return_window(self.market, self.dates, 5, 0)


if __name__ == "__main__":
    unittest.main()
