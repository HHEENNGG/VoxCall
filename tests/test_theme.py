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
    def test_both_schemes_use_same_stylesheet(self):
        """浅色与深色共用同一份 QSS，只是配色不同——保证控件尺寸一致。"""
        light = theme.stylesheet("light")
        dark = theme.stylesheet("dark")
        self.assertIn("QPushButton", light)
        self.assertIn("QPushButton", dark)
        for name in ("window", "field", "border", "muted", "highlight"):
            self.assertNotIn("{" + name + "}", light, "模板占位符未替换")
            self.assertNotIn("{" + name + "}", dark, "模板占位符未替换")
        # 骨架相同：去掉颜色值后结构一致
        self.assertEqual(
            len(light.splitlines()), len(dark.splitlines()), "两套样式的行数应一致"
        )

    def test_colors_differ_per_scheme(self):
        light = theme.colors("light")
        dark = theme.colors("dark")
        self.assertNotEqual(light["window"], dark["window"])
        self.assertNotEqual(light["text"], dark["text"])
        self.assertNotEqual(theme.TRACK["light"], theme.TRACK["dark"])
        self.assertNotEqual(theme.MUTED["light"], theme.MUTED["dark"])

    def test_palette_has_expected_window_color(self):
        pal = theme.palette("dark")
        color = pal.color(pal.ColorRole.Window)
        self.assertEqual(color.name(), "#1f2126")
        light_pal = theme.palette("light")
        self.assertEqual(light_pal.color(light_pal.ColorRole.Window).name(), "#f4f5f7")


if __name__ == "__main__":
    unittest.main()
