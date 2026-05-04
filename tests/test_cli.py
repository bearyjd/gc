"""Tests for gc_cli.cli helpers — _parse_gc_team_map."""

from gc_cli.cli import _parse_gc_team_map


# ---------------------------------------------------------------------------
# _parse_gc_team_map
# ---------------------------------------------------------------------------

def test_parse_gc_team_map_empty_string():
    assert _parse_gc_team_map("") == {}


def test_parse_gc_team_map_valid_pairs():
    raw = "02cdb406-69c7-4e09-96e9-c8e6eda24c6c:Ford,ba97753d-1fe3-42a3-b40c-167ea63086d4:Jack"
    result = _parse_gc_team_map(raw)
    assert result["02cdb406-69c7-4e09-96e9-c8e6eda24c6c"] == "Ford"
    assert result["ba97753d-1fe3-42a3-b40c-167ea63086d4"] == "Jack"


def test_parse_gc_team_map_full_user_env():
    """Parse the actual GC_TEAM_MAP value from the user's production env."""
    raw = (
        "02cdb406-69c7-4e09-96e9-c8e6eda24c6c:Ford,"
        "10212a23-72e6-4bab-a9e3-7d3fb7399102:Ford,"
        "3566ee7d-24fb-4e67-aef5-42ffd41bb481:Penn,"
        "54ec80d7-a06e-4af3-883d-5d5726ecc230:PennJack,"
        "ba97753d-1fe3-42a3-b40c-167ea63086d4:Jack,"
        "e92faf31-63f0-4b2c-bb4f-9a99e6e1963e:Jack,"
        "f94c804e-f3d7-4228-aa55-dad6b4c75d21:Jack"
    )
    result = _parse_gc_team_map(raw)
    assert len(result) == 7
    assert result["54ec80d7-a06e-4af3-883d-5d5726ecc230"] == "PennJack"
    assert result["3566ee7d-24fb-4e67-aef5-42ffd41bb481"] == "Penn"
    assert result["ba97753d-1fe3-42a3-b40c-167ea63086d4"] == "Jack"


def test_parse_gc_team_map_malformed_pairs_skipped_silently():
    """Pairs without a colon are silently ignored."""
    raw = "validid:Child,nocolonhere,anothervalid:Kid"
    result = _parse_gc_team_map(raw)
    assert "nocolonhere" not in result
    assert result["validid"] == "Child"
    assert result["anothervalid"] == "Kid"


def test_parse_gc_team_map_empty_team_id_skipped():
    """Pairs with empty team_id (leading colon) are skipped."""
    raw = ":Child,validid:Kid"
    result = _parse_gc_team_map(raw)
    assert "" not in result
    assert result["validid"] == "Kid"


def test_parse_gc_team_map_whitespace_trimmed():
    """Leading/trailing whitespace around ids and values is stripped."""
    raw = " teamid : Child , teamid2 : Kid "
    result = _parse_gc_team_map(raw)
    assert result["teamid"] == "Child"
    assert result["teamid2"] == "Kid"
