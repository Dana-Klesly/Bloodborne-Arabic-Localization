"""Unit tests for mod_builder with synthetic data only (no game files, no fonts needed)."""
import struct
import unittest

from mod_builder import fromsoft, swf_font


def _fmg_template():
    t = bytearray(0x28)
    t[2] = 2   # version 2
    t[8] = 1
    return bytes(t)


class FmgTests(unittest.TestCase):
    def test_round_trip_keeps_ids_none_and_text(self):
        groups = [(0, 10, 12), (3, 100, 100)]
        strings = {10: "a", 11: None, 12: "سطر\nثانٍ <?x?>", 100: ""}
        data = fromsoft.write_fmg(_fmg_template(), groups, strings)
        self.assertEqual(struct.unpack_from("<I", data, 4)[0], len(data))
        g, s = fromsoft.read_fmg(data)
        self.assertEqual(g, groups)
        self.assertEqual(s[10], "a")
        self.assertIsNone(s[11])
        self.assertEqual(s[12], strings[12])

    def test_ids_cannot_change_with_fixed_groups(self):
        with self.assertRaises(ValueError):
            fromsoft.write_fmg(_fmg_template(), [(0, 1, 2)], {1: "x", 2: "y", 3: "z"})

    def test_regroup_adds_id(self):
        data = fromsoft.write_fmg(_fmg_template(), None, {1: "x", 2: "y", 50: "z"})
        g, s = fromsoft.read_fmg(data)
        self.assertEqual(g, [(0, 1, 2), (2, 50, 50)])
        self.assertEqual(s, {1: "x", 2: "y", 50: "z"})


class DcxTests(unittest.TestCase):
    def test_pack_unpack(self):
        header = bytearray(0x4C)
        header[:4] = b"DCX\0"
        header[0x28:0x2C] = b"DFLT"
        body = b"BND4" + bytes(range(200))
        packed = fromsoft.pack_dcx(bytes(header), body)
        self.assertEqual(fromsoft.unpack_dcx(packed), body)


class ShapeTests(unittest.TestCase):
    def test_encode_decode(self):
        contours = [[("M", 0, 0), ("L", 1000, 0), ("Q", 1500, -500, 1000, -1000), ("L", 0, -1000), ("L", 0, 0)],
                    [("M", 200, -200), ("L", 300, -200), ("L", 300, -300), ("L", 200, -200)]]
        self.assertEqual(swf_font.decode_shape(swf_font.encode_shape(contours)), contours)

    def test_font3_round_trip_and_add(self):
        shape = swf_font.encode_shape([[("M", 0, 0), ("L", 10, 0), ("L", 10, -10), ("L", 0, 0)]])
        f = swf_font.Font3(1, 0x8D, 1, b"T", [shape, shape], [65, 67], 18020, 2460, 0,
                           [100, 120], [b"\x08\x00", b"\x08\x00"], [], b"")
        body = swf_font.build_font3(f)
        self.assertEqual(swf_font.build_font3(swf_font.parse_font3(body)), body)
        g = swf_font.add_glyphs(f, {66: (shape, 50), 0xFE8D: (shape, 0)})
        self.assertEqual(g.codes, [65, 66, 67, 0xFE8D])
        self.assertEqual(g.advances, [100, 50, 120, 0])
        with self.assertRaises(ValueError):
            swf_font.add_glyphs(f, {65: (shape, 1)})


if __name__ == "__main__":
    unittest.main()
