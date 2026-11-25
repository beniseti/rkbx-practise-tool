import pytest

from rkbx_practice.library.camelot import Camelot, KeyRelation, is_compatible, parse_key, relation

TABLE = {
    "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm",
    "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "Dbm",
    "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb",
    "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
}


@pytest.mark.parametrize("code,classic", TABLE.items())
def test_table(code, classic):
    assert str(parse_key(classic)) == code
    assert str(parse_key(code)) == code
    assert parse_key(code).classic_name == classic


@pytest.mark.parametrize("a,b", [
    ("G#m", "Abm"), ("C#", "Db"), ("D#m", "Ebm"), ("A#m", "Bbm"), ("Gb", "F#"),
    ("C#m", "Dbm"), ("Gbm", "F#m"), ("Cb", "B"), ("E#", "F"), ("B♭", "Bb"),
    ("F♯m", "F#m"), ("A minor", "Am"), ("Amin", "Am"), ("C major", "C"), ("08A", "8A"),
    ("8a", "8A"), ("am", "Am"),
])
def test_enharmonics_and_variants(a, b):
    assert parse_key(a) == parse_key(b)


@pytest.mark.parametrize("bad", [None, "", "H", "13A", "0B", "xyz"])
def test_unparseable(bad):
    assert parse_key(bad) is None


def rel(a, b):
    return relation(parse_key(a), parse_key(b))


def test_relations():
    assert rel("8A", "8A") == KeyRelation.SAME
    assert rel("8A", "9A") == KeyRelation.ADJACENT
    assert rel("8A", "7A") == KeyRelation.ADJACENT
    assert rel("12B", "1B") == KeyRelation.ADJACENT
    assert rel("1A", "12A") == KeyRelation.ADJACENT
    assert rel("8A", "8B") == KeyRelation.RELATIVE
    assert rel("8A", "10A") == KeyRelation.ENERGY_BOOST
    assert rel("11A", "1A") == KeyRelation.ENERGY_BOOST
    assert rel("8A", "3A") == KeyRelation.SEMITONE_UP  # 8+7=15 -> 3
    assert rel("8A", "6A") == KeyRelation.NONE           # -2 no es energy boost
    assert rel("8A", "9B") == KeyRelation.NONE


def test_strictness():
    a, boost, semi = parse_key("8A"), parse_key("10A"), parse_key("3A")
    assert not is_compatible(a, boost, "strict")
    assert is_compatible(a, boost, "moderate")
    assert not is_compatible(a, semi, "moderate")
    assert is_compatible(a, semi, "relaxed")


def test_camelot_validation():
    with pytest.raises(ValueError):
        Camelot(13, "A")
