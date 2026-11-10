import unittest
from clamp import clamp


class ClampTests(unittest.TestCase):
    def test_below(self):
        self.assertEqual(clamp(-1, 0, 10), 0)

    def test_inside(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_above(self):
        self.assertEqual(clamp(20, 0, 10), 10)

    def test_equal_bounds(self):
        self.assertEqual(clamp(7, 4, 4), 4)

    def test_reversed(self):
        with self.assertRaises(ValueError):
            clamp(7, 10, 0)


if __name__ == '__main__':
    unittest.main()
