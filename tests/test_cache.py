"""JSON-backed seen-message cache."""

import json
import pathlib

from use_agent import cache


def test_load_missing_file_is_empty(tmp_path: pathlib.Path) -> None:
    c = cache.Cache.load(tmp_path / 'nope.json')
    assert len(c) == 0


def test_add_is_idempotent(tmp_path: pathlib.Path) -> None:
    c = cache.Cache.load(tmp_path / 'c.json')
    c.add('id-1')
    stamp = c._entries['id-1']
    c.add('id-1')
    # Re-adding an existing id must not bump the timestamp.
    assert c._entries['id-1'] == stamp
    assert len(c) == 1


def test_contains_uses_membership_protocol(tmp_path: pathlib.Path) -> None:
    c = cache.Cache.load(tmp_path / 'c.json')
    c.add('id-1')
    assert 'id-1' in c
    assert 'id-2' not in c
    # Non-string lookups must never match.
    assert 42 not in c


def test_save_load_roundtrip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'c.json'
    a = cache.Cache.load(path)
    a.add('id-1')
    a.add('id-2')
    a.save()

    b = cache.Cache.load(path)
    assert len(b) == 2
    assert 'id-1' in b
    assert 'id-2' in b


def test_save_creates_parent_dir(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'nested' / 'dir' / 'cache.json'
    c = cache.Cache.load(path)
    c.add('id-1')
    c.save()
    assert path.exists()
    assert json.loads(path.read_text(encoding='utf-8')) == {
        'id-1': c._entries['id-1']
    }


def test_retain_drops_missing_and_returns_count(
    tmp_path: pathlib.Path,
) -> None:
    c = cache.Cache.load(tmp_path / 'c.json')
    for mid in ('id-1', 'id-2', 'id-3'):
        c.add(mid)

    dropped = c.retain({'id-1', 'id-3', 'id-never-existed'})

    assert dropped == 1
    assert 'id-1' in c
    assert 'id-2' not in c
    assert 'id-3' in c
    assert len(c) == 2


def test_retain_empty_wipes_cache(tmp_path: pathlib.Path) -> None:
    c = cache.Cache.load(tmp_path / 'c.json')
    c.add('id-1')
    c.add('id-2')
    dropped = c.retain(())
    assert dropped == 2
    assert len(c) == 0


def test_load_corrupt_json_is_empty(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'c.json'
    path.write_text('{not json', encoding='utf-8')
    c = cache.Cache.load(path)
    assert len(c) == 0


def test_load_empty_file_is_empty(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'c.json'
    path.write_text('', encoding='utf-8')
    c = cache.Cache.load(path)
    assert len(c) == 0


def test_load_non_dict_json_is_empty(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'c.json'
    path.write_text('[1, 2, 3]', encoding='utf-8')
    c = cache.Cache.load(path)
    assert len(c) == 0
