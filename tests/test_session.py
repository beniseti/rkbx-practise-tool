import random

from rkbx_practice.config import SessionConfig
from rkbx_practice.library import Track
from rkbx_practice.session import PracticeSession, bpm_match, generate_session, transition


def T(id, bpm, key):
    return Track.build(id=id, title=f"T{id}", bpm=bpm, key=key)


def test_bpm_match_tolerance_and_half_double():
    assert bpm_match(128, 130, 4, False).compatible
    assert not bpm_match(128, 140, 4, False).compatible
    m = bpm_match(87, 174, 4, True)
    assert m.compatible and m.multiplier == 2.0
    m = bpm_match(174, 86, 4, True)
    assert m.compatible and m.multiplier == 0.5
    assert not bpm_match(87, 174, 4, False).compatible


def test_transition_respects_camelot_toggle():
    a, b = T(1, 128, "8A"), T(2, 128, "2A")
    assert not transition(a, b, SessionConfig()).compatible
    assert transition(a, b, SessionConfig(camelot_enabled=False)).compatible


def test_generate_excludes_unanalyzed(library, session_cfg):
    gen = generate_session(library.tracks_of("11"), session_cfg, rng=random.Random(1))
    excluded_ids = {t.id for t, _ in gen.excluded}
    assert "2001" in excluded_ids
    assert all(t.bpm and t.camelot for t in gen.tracks)
    assert len(gen.tracks) + len(gen.excluded) == len(library.tracks_of("11"))


def test_order_prefers_smooth_chain(session_cfg):
    tracks = [T(1, 124, "8A"), T(2, 140, "3B"), T(3, 125, "9A"), T(4, 126, "10A"),
              T(5, 141, "3B"), T(6, 124, "8B")]
    gen = generate_session(tracks, session_cfg, rng=random.Random(0))
    ids = [t.id for t in gen.tracks]
    assert sorted(ids) == sorted(t.id for t in tracks)
    # Los dos temas a 140 deben ir juntos.
    pos = {tid: i for i, tid in enumerate(ids)}
    assert abs(pos["2"] - pos["5"]) == 1
    assert gen.incompatible_jumps <= 1


def test_order_with_start(session_cfg):
    tracks = [T(1, 124, "8A"), T(2, 125, "9A"), T(3, 126, "10A")]
    gen = generate_session(tracks, session_cfg, start=tracks[2])
    assert gen.tracks[0].id == "3"


def make_session(cfg, wildcard=0.0, seed=0, follow_list=False):
    cfg.wildcard_probability = wildcard
    cfg.follow_list = follow_list
    tracks = [T(i, 124 + i * 0.5, k) for i, k in
              enumerate(["8A", "8A", "9A", "9A", "10A", "10A", "11A", "11A"], start=1)]
    gen = generate_session(tracks, cfg, rng=random.Random(seed))
    return PracticeSession.from_generated("test", gen, cfg, rng=random.Random(seed)), tracks


def test_strike_follow_plan(session_cfg):
    s, _ = make_session(session_cfg)
    plan_before = [t.id for t in s.plan]
    first = s.suggestion.track
    assert first.id == plan_before[0]
    s.track_loaded(first, deck=1)
    assert s.is_struck(first.id)
    assert [t.id for t in s.plan] == plan_before  # se siguió el plan: no se recalcula
    assert s.suggestion.track.id == plan_before[1]


def test_recalc_on_unexpected_load(session_cfg):
    s, tracks = make_session(session_cfg)
    s.track_loaded(s.suggestion.track, deck=1)
    unexpected = s.plan[-1]
    s.track_loaded(unexpected, deck=2)
    assert s.is_struck(unexpected.id)
    assert s.anchor.id == unexpected.id
    # El plan: lo tocado primero, después el resto encadenado desde el tema cargado.
    assert [t.id for t in s.plan[:2]] == s.load_order
    nxt = s.suggestion.track
    assert not s.is_struck(nxt.id)
    assert transition(unexpected, nxt, session_cfg).compatible


def test_unstrike(session_cfg):
    s, _ = make_session(session_cfg)
    a = s.suggestion.track
    s.track_loaded(a, deck=1)
    b = s.suggestion.track
    s.track_loaded(b, deck=2)
    assert s.unstrike(b.id)
    assert not s.is_struck(b.id)
    assert s.anchor.id == a.id
    assert b.id in [t.id for t in s.remaining]
    assert not s.unstrike(b.id)


def test_external_track_is_anchor(session_cfg):
    s, _ = make_session(session_cfg)
    outsider = T(99, 129, "11A")
    assert s.track_loaded(outsider, deck=1) is False
    assert s.anchor.id == "99"
    assert not s.struck


def test_wildcard(session_cfg):
    s, _ = make_session(session_cfg, wildcard=1.0)
    s.track_loaded(s.plan[0], deck=1)
    sug = s.suggestion
    assert sug.is_wildcard
    assert transition(s.anchor, sug.track, session_cfg).compatible


def test_session_completes(session_cfg):
    s, tracks = make_session(session_cfg, wildcard=0.3, seed=5)
    for _ in range(len(tracks)):
        s.track_loaded(s.suggestion.track, deck=1)
    assert s.suggestion is None
    assert len(s.struck) == len(tracks)


def test_display_order_is_stable(session_cfg):
    s, _ = make_session(session_cfg)
    shown = [t.id for t, _ in s.entries()]
    s.track_loaded(s.plan[-1], deck=1)          # tema distinto del sugerido: hay recálculo
    after = s.entries()
    assert [t.id for t, _ in after] == shown    # la lista visible no se reordena
    assert dict((t.id, struck) for t, struck in after)[shown[-1]] is True


def test_skipped_suggestion_is_marked(session_cfg):
    s, _ = make_session(session_cfg)
    sug = s.suggestion.track
    other = next(t for t in s.plan if t.id != sug.id)
    s.track_loaded(other, deck=1)
    assert s.is_skipped(sug.id)
    s.track_loaded(sug, deck=2)                 # si luego se carga, deja de estar saltado
    assert not s.is_skipped(sug.id) and s.is_struck(sug.id)


def test_variety_changes_order_but_keeps_compatibility(library):
    from rkbx_practice.config import SessionConfig
    tracks = library.tracks_of("10")
    cfg = SessionConfig(optimize_time_budget_s=0.1, variety=0.5)
    orders = {tuple(t.id for t in generate_session(tracks, cfg, rng=random.Random(seed)).tracks)
              for seed in range(5)}
    assert len(orders) > 1                                  # sesiones distintas
    same = generate_session(tracks, cfg, rng=random.Random(3)).tracks
    assert [t.id for t in same] == [t.id for t in generate_session(tracks, cfg, rng=random.Random(3)).tracks]
    base = generate_session(tracks, SessionConfig(optimize_time_budget_s=0.1, variety=0.0), rng=random.Random(0))
    varied = generate_session(tracks, cfg, rng=random.Random(1))
    assert varied.incompatible_jumps <= base.incompatible_jumps + 2


def test_adjacent_keys_are_compatible_both_ways():
    cfg = SessionConfig()
    a = T(1, 128, "2A")
    assert transition(a, T(2, 128, "1A"), cfg).compatible
    assert transition(a, T(3, 128, "3A"), cfg).compatible
    assert transition(a, T(4, 128, "1A"), SessionConfig(camelot_strictness="strict")).compatible


def test_follow_list_suggests_next_in_list(session_cfg):
    s, _ = make_session(session_cfg, follow_list=True)
    order = [t.id for t in s.plan]
    assert s.suggestion.track.id == order[0]
    s.track_loaded(s.plan[0], deck=1)
    assert s.suggestion.track.id == order[1]
    s.track_loaded(s._by_id[order[4]], deck=2)            # salta a la posición 5
    assert [t.id for t in s.plan] == order                 # la lista no cambia nunca
    assert s.suggestion.track.id == order[5]               # sigue desde ahí
    assert s.is_skipped(order[1])                          # la sugerida se marca saltada
    s.track_loaded(T(99, 140, "1B"), deck=1)               # tema de fuera: no mueve la posición
    assert s.suggestion.track.id == order[5]
    s.unstrike(order[4])                                    # destachar vuelve a la posición anterior
    assert s.suggestion.track.id == order[1]
