from app.paths import safe_path_name


def test_safe_path_name_preserves_readable_playlist_title() -> None:
    assert safe_path_name("课程 第 1 章") == "课程 第 1 章"


def test_safe_path_name_replaces_unsafe_characters_and_trims_edges() -> None:
    assert safe_path_name('  A/B:C*D?"E<>|.  ') == "A_B_C_D__E___"


def test_safe_path_name_falls_back_when_title_is_empty() -> None:
    assert safe_path_name("...   ", fallback="playlist-job123") == "playlist-job123"
