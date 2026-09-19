from hypothesis import given
from hypothesis import strategies as st
from werkzeug.datastructures import MultiDict

from world_stage.utils.playlist_options import playlist_options


@st.composite
def boolean_values(draw):
    value = draw(st.booleans())
    choices = ["1", "on", "true", "yes"] if value else ["0", "off", "false", "no"]
    word = draw(st.sampled_from(choices))
    cases = draw(st.lists(st.booleans(), min_size=len(word), max_size=len(word)))
    return value, "".join(
        letter.upper() if upper else letter for letter, upper in zip(word, cases, strict=True)
    )


@given(
    postcards=boolean_values(), host=boolean_values(), intervals=boolean_values(),
    flags=st.permutations(["nh", "ni", "np"]),
)
def test_query_options_override_suffixes_and_name_downloads(postcards, host, intervals, flags):
    args = MultiDict([
        ("Postcards", postcards[1]), ("HOST", host[1]), ("intervals", intervals[1])
    ])
    options = playlist_options("1997sf1-" + "-".join(flags), args, show=True)
    assert (options.postcards, options.host, options.intervals) == (
        postcards[0], host[0], intervals[0]
    )
    assert options.filename == "1997sf1" + "".join(
        suffix for suffix, enabled in [
            ("-nh", host[0]), ("-ni", intervals[0]), ("-np", postcards[0])
        ]
        if not enabled
    )


@given(flags=st.lists(st.sampled_from(["nh", "ni", "np"]), unique=True), show=st.booleans())
def test_missing_options_are_false_with_any_legacy_suffixes(flags, show):
    if not show:
        flags = [flag for flag in flags if flag == "np"]
    key = "1997sf1" + "".join(f"-{flag}" for flag in flags)
    options = playlist_options(key, MultiDict(), show=show)
    assert options.filename == "1997sf1" + ("-nh-ni-np" if show else "-np")
    assert options.stem == "1997sf1"
    assert options.postcards is False
    assert options.host is False
    assert options.intervals is False


@given(postcards=st.booleans(), host=st.booleans(), intervals=st.booleans(), show=st.booleans())
def test_native_checkbox_options_enable_only_checked_values(postcards, host, intervals, show):
    checked = {"postcards": postcards, "host": host, "intervals": intervals}
    args = MultiDict((name, "on") for name, enabled in checked.items() if enabled)
    options = playlist_options("1997sf1", args, show=show)
    assert options.postcards == postcards
    assert options.host == (show and host)
    assert options.intervals == (show and intervals)


@given(name=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1),
       audio=st.booleans())
def test_m3u_manifest_resolution_uses_the_declared_source(name, audio):
    import json
    from unittest.mock import MagicMock, patch

    from world_stage.utils.playlist_media import resolve_playlist_media

    url = f"https://media.world-stage.org/{name}.json"
    source = f"https://media.world-stage.org/{name}.{'m4a' if audio else 'mov'}"
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({
        "sources": [{"url": source}]
    }).encode()
    with patch('world_stage.utils.playlist_media.urlopen', return_value=response):
        assert resolve_playlist_media(url) == source
        assert resolve_playlist_media(source) == source


@given(invalid=st.one_of(st.none(), st.integers(), st.booleans(), st.just("")),
       valid_source=st.booleans())
def test_m3u_manifests_do_not_treat_missing_source_urls_as_media(invalid, valid_source):
    import json
    from unittest.mock import MagicMock, patch

    import pytest

    from world_stage.utils.playlist_media import PlaylistMediaError, resolve_playlist_media

    url = "https://media.world-stage.org/manifest.json"
    media = "https://media.world-stage.org/video.mov"
    sources = [{"url": invalid}]
    if valid_source:
        sources.append({"url": media})
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({"sources": sources}).encode()
    with patch("world_stage.utils.playlist_media.urlopen", return_value=response):
        if valid_source:
            assert resolve_playlist_media(url) == media
        else:
            with pytest.raises(PlaylistMediaError):
                resolve_playlist_media(url)
