"""The regex fast path must give the same result as the streaming parser."""

import gzip
import json

from custom_components.ndw_laadpalen import parser

from .conftest import HOME_LAT, HOME_LON, NDW_FEED


def _feed(**kwargs) -> bytes:
    return gzip.compress(json.dumps({**NDW_FEED, "type": "FeatureCollection"}, **kwargs).encode())


def test_fast_path_matches_streaming_parser():
    # NDW's own layout is compact and starts every feature with its id
    compact = _feed(separators=(",", ":"))
    data = gzip.decompress(compact)
    near = lambda lat, lon: parser.distance_m(HOME_LAT, HOME_LON, lat, lon) <= 200  # noqa: E731
    fast = parser._scan_fast(data, near, None)
    assert fast is not None, "the fast path must recognise the real feed layout"
    assert fast == parser._scan_stream(data, near, None)
    assert set(fast) == {"NL-NUO-near", "NL-NUO-two"}
    assert set(parser.find_by_ids(compact, {"NL-NUO-far"})) == {"NL-NUO-far"}


def test_other_layout_falls_back_to_streaming():
    spaced = _feed()  # json.dumps default separators: not the real layout
    assert parser._scan_fast(gzip.decompress(spaced), None, None) is None
    assert set(parser.find_by_ids(spaced, {"NL-NUO-near"})) == {"NL-NUO-near"}
