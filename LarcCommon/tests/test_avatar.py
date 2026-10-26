"""Couleur d'avatar stable entre lancements (indépendante de PYTHONHASHSEED)."""
import os
import subprocess
import sys
import zlib

from larccommon.widgets.avatar import _COLORS, avatar_color_index

NAMES = [("KOFFI", "Ama"), ("MENSAH", "Kodjo"), ("AGBO", "Délali"), ("", "")]


def test_index_is_crc32_of_utf8():
    for last, first in NAMES:
        expected = zlib.crc32((last + first).encode("utf-8")) % len(_COLORS)
        assert avatar_color_index(last, first) == expected


def test_index_in_palette_range():
    for last, first in NAMES:
        assert 0 <= avatar_color_index(last, first) < len(_COLORS)


def _indices_with_seed(seed: str) -> str:
    code = (
        "from larccommon.widgets.avatar import avatar_color_index\n"
        f"print([avatar_color_index(l, f) for l, f in {NAMES!r}])\n"
    )
    env = dict(os.environ, PYTHONHASHSEED=seed, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return out.stdout.strip()


def test_same_color_across_hash_seeds():
    assert _indices_with_seed("1") == _indices_with_seed("4242")
