import unittest
from punchpad_app.tui.kiosk_screen import render_banner, prompt_pin


class TestKioskScreen(unittest.TestCase):
    def test_render_banner_contains_strings(self):
        out = render_banner("ok_in", "Hello", "World")
        self.assertTrue(out)
        self.assertIn("PUNCHED IN", out)
        self.assertIn("Hello", out)
        self.assertIn("World", out)

    def test_prompt_pin_basic_and_backspace(self):
        # Sequence: '1','2','3','4','\n'
        seq = list("1234\n")

        def getch():
            return seq.pop(0)

        pin = prompt_pin(getch, echo=False)
        self.assertEqual(pin, "1234")

        # Now with backspace handling: '1','2','\b','3','\n' => '13'
        seq2 = ["1", "2", "\x08", "3", "\n"]

        def getch2():
            return seq2.pop(0)

        pin2 = prompt_pin(getch2, echo=False)
        self.assertEqual(pin2, "13")

    def test_prompt_pin_esc_interrupts(self):
        seq = ["\x1b"]  # Esc

        def getch():
            return seq.pop(0)

        with self.assertRaises(KeyboardInterrupt):
            prompt_pin(getch, echo=False)


if __name__ == "__main__":
    unittest.main()
