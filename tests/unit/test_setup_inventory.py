import json
from types import SimpleNamespace
from src.infrastructure.services import setup_inventory as inventory


def test_only_exact_file_size_and_path_are_reused(monkeypatch, tmp_path):
    model = {'id': 'main', 'repo': 'Example/Chat', 'filename': 'model.gguf', 'size_bytes': 4, 'sha256': '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'}
    monkeypatch.setattr(inventory, 'catalog', lambda: [model])
    monkeypatch.setattr(inventory.lm, 'find_cli', lambda: 'fictional-cli')
    monkeypatch.setattr(inventory.lm, '_cli', lambda args: SimpleNamespace(stdout=json.dumps([
        {'modelKey': 'existing-alias', 'path': 'Example/Chat/model.gguf', 'sizeBytes': 4, 'type': 'llm'}])))
    assert inventory.installed_assets(str(tmp_path)) == {}
    target = tmp_path / model['repo'] / model['filename']
    target.parent.mkdir(parents=True)
    target.write_bytes(b'test')
    assert inventory.installed_assets(str(tmp_path)) == {'main': 'existing-alias'}
    assert inventory.installed_assets(str(tmp_path), verify=True) == {'main': 'existing-alias'}
    target.write_bytes(b'fake')
    assert inventory.installed_assets(str(tmp_path)) == {'main': 'existing-alias'}
    assert inventory.installed_assets(str(tmp_path), verify=True) == {}
    target.write_bytes(b'wrong-size')
    assert inventory.installed_assets(str(tmp_path)) == {}
