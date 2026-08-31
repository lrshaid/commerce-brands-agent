import unittest

from agent.analysis.decomposition import (
    additive_decomposition,
    mix_decomposition,
    multiplicative_decomposition,
    ratio_decomposition,
)
from agent.analysis.nmv_tree import nmv_decomposition_tree


class DecompositionTests(unittest.TestCase):
    def test_additive_reconciles(self):
        result = additive_decomposition(
            {"gmv": 100.0, "emv": 10.0, "rmv": -20.0},
            {"gmv": 110.0, "emv": 8.0, "rmv": -18.0},
        )
        self.assertTrue(result["check"]["exact"])
        self.assertAlmostEqual(result["check"]["headline_change_pct"], 100 / 9)

    def test_lmdi_is_exact_and_order_independent(self):
        first = multiplicative_decomposition(
            {"traffic": 100.0, "cvr": 0.02, "aov": 50.0},
            {"traffic": 110.0, "cvr": 0.022, "aov": 55.0},
        )
        second = multiplicative_decomposition(
            {"aov": 50.0, "traffic": 100.0, "cvr": 0.02},
            {"aov": 55.0, "traffic": 110.0, "cvr": 0.022},
        )
        self.assertTrue(first["check"]["exact"])
        self.assertEqual(first["method"], "lmdi_i")
        for factor in ("traffic", "cvr", "aov"):
            self.assertAlmostEqual(
                first["contributions_pct_points"][factor],
                second["contributions_pct_points"][factor],
            )

    def test_ratio_denominator_fall_is_positive(self):
        result = ratio_decomposition(10.0, 100.0, 10.0, 80.0)
        self.assertTrue(result["check"]["exact"])
        self.assertGreater(result["contributions_pct_points"]["denominator"], 0)

    def test_nonpositive_factor_uses_exact_sequential_fallback(self):
        result = multiplicative_decomposition(
            {"a": 2.0, "b": -1.0}, {"a": 3.0, "b": -2.0}
        )
        self.assertEqual(result["method"], "sequential")
        self.assertTrue(result["order_dependent"])
        self.assertTrue(result["check"]["exact"])

    def test_mix_midpoint_identity_reconciles(self):
        result = mix_decomposition(
            {
                "a": {"weight": 0.5, "rate": 0.10},
                "b": {"weight": 0.5, "rate": 0.20},
            },
            {
                "a": {"weight": 0.7, "rate": 0.11},
                "b": {"weight": 0.3, "rate": 0.19},
            },
        )
        self.assertTrue(result["check"]["exact"])
        total_mix = sum(
            segment["mix_effect_pct_points"]
            for segment in result["segments"].values()
        )
        self.assertNotAlmostEqual(total_mix, 0.0)

    def test_nmv_tree_reconciles(self):
        result = nmv_decomposition_tree(
            {
                "gmv": 100.0,
                "emv": 5.0,
                "rmv": -15.0,
                "gmv_channels": {
                    "web": {"traffic": 100.0, "cvr": 0.02, "aov": 50.0}
                },
            },
            {
                "gmv": 110.0,
                "emv": 6.0,
                "rmv": -16.0,
                "gmv_channels": {
                    "web": {"traffic": 110.0, "cvr": 0.02, "aov": 50.0}
                },
            },
        )
        self.assertTrue(result["exact"])

    def test_nmv_tree_rejects_positive_rmv(self):
        with self.assertRaisesRegex(ValueError, "stored negative"):
            nmv_decomposition_tree(
                {"gmv": 100.0, "emv": 5.0, "rmv": 15.0},
                {"gmv": 110.0, "emv": 6.0, "rmv": -16.0},
            )


if __name__ == "__main__":
    unittest.main()

