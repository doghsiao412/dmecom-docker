import colorsys
import unittest
import theme
import ui_layout
import live_ui

def lightness(value):
    rgb, _ = theme.parse(value)
    return colorsys.rgb_to_hls(*rgb)[1]

class ThemeTests(unittest.TestCase):
    def test_roles(self):
        self.assertLess(lightness(theme.surface('#f4f7fc')), 0.25)       # light tint -> dark surface
        self.assertEqual(theme.surface('#2463cf'), '#2463cf')             # saturated fill kept
        self.assertEqual(theme.surface('#fff'), '#151c26')                # cards use the fixed surface
        self.assertGreater(lightness(theme.text('#1d2939')), 0.8)         # dark ink -> light ink
        self.assertEqual(theme.text('#fff'), '#fff')                      # white on coloured fill stays
        self.assertGreater(lightness(theme.text('#2463cf')), 0.6)         # accent text brightened
        self.assertTrue(theme.shadow('#21345b03').startswith('#000000'))  # neutral shadow -> black
        self.assertFalse(theme.shadow('#20a27415').startswith('#000000')) # coloured glow keeps hue
        self.assertEqual(theme.surface('#f5f7fbef'), '#0d1219ef')         # translucent top bar

    def test_dark_css_structure(self):
        css = 'a{color:#1d2939;padding:4px}@media(max-width:500px){b{background:white;margin:0}}@keyframes k{0%{color:#000}}c{--x:#fff;width:1px}'
        dark = theme.dark_css(css)
        self.assertTrue(dark.startswith('@media (prefers-color-scheme:dark){'))
        self.assertIn('a{color:', dark); self.assertNotIn('padding', dark)
        self.assertIn('@media(max-width:500px){b{background:#151c26}}', dark)
        self.assertNotIn('keyframes', dark); self.assertNotIn('c{', dark)
        self.assertEqual(dark.count('{'), dark.count('}'))

    def test_pages_ship_both_schemes(self):
        self.assertIn('@media (prefers-color-scheme:dark){:root{color-scheme:dark', ui_layout.CSS)
        light = ui_layout.CSS[:ui_layout.CSS.index('@media (prefers-color-scheme:dark)')]
        self.assertIn('color-scheme:light', light)
        html = ui_layout.page_shell('錄音與備份', '<p>x</p>', '', {})
        self.assertIn('<meta name="color-scheme" content="light dark">', html)
        self.assertIn('prefers-color-scheme:dark', live_ui.render_live('nonce'))

if __name__ == '__main__': unittest.main()
