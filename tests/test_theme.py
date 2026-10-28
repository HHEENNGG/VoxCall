import unittest
from unittest import mock

from app import theme


class ResolveTests(unittest.TestCase):
    def test_explicit_prefs(self):
        self.assertEqual(theme.resolve("light"), "light")
        self.assertEqual(theme.resolve("dark"), "dark")
        self.assertEqual(theme.resolve("DARK"), "dark")

    def test_auto_follows_system(self):
        with mock.patch.object(theme, "system_is_dark", return_value=True):
            self.assertEqual(theme.resolve("auto"), "dark")
        with mock.patch.object(theme, "system_is_dark", return_value=False):
            self.assertEqual(theme.resolve("auto"), "light")

    def test_unknown_pref_falls_back_to_auto(self):
        with mock.patch.object(theme, "system_is_dark", return_value=False):
            self.assertEqual(theme.resolve("purple"), "light")


class StyleTests(unittest.TestCase):
    def test_dark_has_stylesheet_light_has_none(self):
        self.assertIn("QPushButton", theme.stylesheet("dark"))
        self.assertEqual(theme.stylesheet("light"), "")

    def test_colors_differ_per_scheme(self):
        self.assertNotEqual(theme.TRACK["light"], theme.TRACK["dark"])
        self.assertNotEqual(theme.MUTED["light"], theme.MUTED["dark"])

    def test_palette_has_expected_window_color(self):
        pal = theme.palette("dark")
        color = pal.color(pal.ColorRole.Window)
        self.assertEqual(color.name(), "#1f2126")


if __name__ == "__main__":
    unittest.main()
