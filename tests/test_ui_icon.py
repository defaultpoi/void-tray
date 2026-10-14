import unittest

from facade import rt


class TestTempIconLayout(unittest.TestCase):
    def test_digits_and_ring_are_centred_together(self):
        left, cx, cy = rt.temp_icon_layout(24, 12.0, 9.0)
        ring_outer = 2 * rt.TEMP_RING_RADIUS + rt.TEMP_RING_LINE_WIDTH
        right_edge = cx + ring_outer / 2
        self.assertAlmostEqual(left, 24 - right_edge, places=6)  # equal margins either side

    def test_ring_follows_the_digits_and_sits_at_their_top(self):
        left, cx, cy = rt.temp_icon_layout(24, 12.0, 9.0)
        self.assertGreater(cx, left + 12.0)             # to the right of the digits
        digits_top = 24 / 2 - 9.0 / 2
        self.assertAlmostEqual(cy - (2 * rt.TEMP_RING_RADIUS + rt.TEMP_RING_LINE_WIDTH) / 2, digits_top, places=6)

    def test_ring_never_leaves_the_icon_top(self):
        _left, _cx, cy = rt.temp_icon_layout(24, 12.0, 40.0)
        self.assertGreaterEqual(cy - rt.TEMP_RING_RADIUS - rt.TEMP_RING_LINE_WIDTH / 2, -1e-9)


if __name__ == '__main__':
    unittest.main()
