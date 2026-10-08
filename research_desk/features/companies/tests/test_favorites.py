"""features.companies.favorites: the favorites JSON file.

The first nine tests are ported unchanged from langgraph_tagger/analytics/tests/test_favorites.py
(only the import moved). Added below them: more odd file shapes and the atomic write.
"""
import json
from pathlib import Path

import pytest

from research_desk.features.companies import favorites


def test_load_missing_file_returns_empty(tmp_path: Path):
    path = tmp_path / 'favs.json'
    result = favorites.load(path)
    assert result == []
    assert not path.exists()


def test_load_creates_parent_dir_on_add(tmp_path: Path):
    path = tmp_path / 'sub' / 'favs.json'
    favorites.add(path, '005930')
    assert path.exists()
    assert json.loads(path.read_text(encoding='utf-8')) == {'stocks': ['005930']}


def test_add_is_idempotent(tmp_path: Path):
    path = tmp_path / 'favs.json'
    favorites.add(path, '005930')
    favorites.add(path, '005930')
    favorites.add(path, '005930')
    assert favorites.load(path) == ['005930']


def test_add_preserves_order(tmp_path: Path):
    path = tmp_path / 'favs.json'
    favorites.add(path, '005930')
    favorites.add(path, '000660')
    favorites.add(path, '373220')
    assert favorites.load(path) == ['005930', '000660', '373220']


def test_remove_existing(tmp_path: Path):
    path = tmp_path / 'favs.json'
    favorites.add(path, '005930')
    favorites.add(path, '000660')
    favorites.remove(path, '005930')
    assert favorites.load(path) == ['000660']


def test_remove_absent_is_noop(tmp_path: Path):
    path = tmp_path / 'favs.json'
    favorites.add(path, '005930')
    favorites.remove(path, '999999')
    assert favorites.load(path) == ['005930']


def test_corrupted_json_backs_up_and_returns_empty(tmp_path: Path):
    path = tmp_path / 'favs.json'
    path.write_text('not valid json {', encoding='utf-8')
    result = favorites.load(path)
    assert result == []
    bak = path.with_suffix('.json.bak')
    assert bak.exists()
    assert bak.read_text(encoding='utf-8') == 'not valid json {'


def test_corrupted_then_add_recovers(tmp_path: Path):
    """After .bak backup of corrupted file, a subsequent add must work cleanly."""
    path = tmp_path / 'favs.json'
    path.write_text('bad json', encoding='utf-8')
    favorites.add(path, '005930')          # must not raise
    assert favorites.load(path) == ['005930']
    assert path.with_suffix('.json.bak').exists()


def test_load_non_dict_json_backs_up(tmp_path: Path):
    """Valid JSON but wrong shape (e.g. a list, null, int) must trigger .bak recovery."""
    path = tmp_path / 'favs.json'
    path.write_text('["005930", "000660"]', encoding='utf-8')   # list, not dict
    result = favorites.load(path)
    assert result == []
    assert path.with_suffix('.json.bak').exists()


# --- added: odd shapes and the atomic write ----------------------------------------------------

@pytest.mark.parametrize('content', ['null', '5', '"005930"', '{"stocks": "005930"}', '{"stocks": null}'])
def test_odd_shapes_move_to_bak_and_read_as_empty(tmp_path: Path, content):
    path = tmp_path / 'favs.json'
    path.write_text(content, encoding='utf-8')
    assert favorites.load(path) == []
    assert not path.exists()
    assert path.with_suffix('.json.bak').read_text(encoding='utf-8') == content


def test_object_without_stocks_reads_as_empty_and_stays(tmp_path: Path):
    path = tmp_path / 'favs.json'
    path.write_text('{}', encoding='utf-8')
    assert favorites.load(path) == []
    assert path.read_text(encoding='utf-8') == '{}'
    assert not path.with_suffix('.json.bak').exists()


def test_write_goes_through_a_temp_file_in_the_same_folder(tmp_path: Path, monkeypatch):
    path = tmp_path / 'favs.json'
    replaced = []
    real_replace = favorites.os.replace

    def spy(src, dst):
        replaced.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(favorites.os, 'replace', spy)
    favorites.add(path, '005930')
    ((src, dst),) = replaced
    assert dst == path
    assert src.parent == path.parent and src != path
    assert sorted(p.name for p in tmp_path.iterdir()) == ['favs.json']


def test_failed_write_keeps_the_old_file_and_leaves_no_temp(tmp_path: Path, monkeypatch):
    path = tmp_path / 'favs.json'
    favorites.add(path, '005930')
    before = path.read_bytes()

    def refuse(src, dst):
        raise PermissionError('locked by another program')

    monkeypatch.setattr(favorites.os, 'replace', refuse)
    with pytest.raises(PermissionError):
        favorites.add(path, '000660')
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['favs.json']
