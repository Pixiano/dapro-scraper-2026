import pytest

from backend.youtube.client import YouTubeError
from backend.youtube.resolver import parse_input

UC = "UC" + "x" * 22
VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize("raw,kind,value", [
    # bare forms
    (UC, "channel_id", UC),
    ("@mkbhd", "handle", "@mkbhd"),
    ("mkbhd", "name", "mkbhd"),
    ("  mkbhd  ", "name", "mkbhd"),
    # channel URLs
    (f"https://www.youtube.com/channel/{UC}", "channel_id", UC),
    (f"youtube.com/channel/{UC}", "channel_id", UC),
    ("https://www.youtube.com/@mkbhd", "handle", "@mkbhd"),
    ("https://m.youtube.com/@mkbhd/videos", "handle", "@mkbhd"),
    ("https://www.youtube.com/user/marquesbrownlee", "username", "marquesbrownlee"),
    ("https://www.youtube.com/c/mkbhd", "name", "mkbhd"),
    # video URLs
    (f"https://www.youtube.com/watch?v={VID}", "video_id", VID),
    (f"https://www.youtube.com/watch?v={VID}&t=42s", "video_id", VID),
    (f"https://youtu.be/{VID}", "video_id", VID),
    (f"https://youtu.be/{VID}?si=abc", "video_id", VID),
    (f"https://www.youtube.com/shorts/{VID}", "video_id", VID),
    (f"https://www.youtube.com/live/{VID}", "video_id", VID),
    (f"https://www.youtube.com/embed/{VID}", "video_id", VID),
    (f"https://music.youtube.com/watch?v={VID}", "video_id", VID),
])
def test_parse_matrix(raw, kind, value):
    p = parse_input(raw)
    assert (p.kind, p.value) == (kind, value)


@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "https://vimeo.com/12345",
    "https://www.youtube.com/",
    "https://www.youtube.com/watch?x=1",
    "https://www.youtube.com/channel/notachannelid",
    "https://youtu.be/short",
])
def test_parse_rejects(raw):
    with pytest.raises(YouTubeError) as e:
        parse_input(raw)
    assert e.value.status == 400


def test_bare_channel_id_must_be_exact():
    # 23-char and 25-char UC-prefixed strings are names, not IDs
    assert parse_input("UC" + "x" * 21).kind == "name"
    assert parse_input("UC" + "x" * 23).kind == "name"
