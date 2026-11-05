import hashlib
import importlib.util
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest


def test_pinned_staging_rejects_archive_mismatch_before_writing(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('prepare_llmfit', Path('scripts/setup/prepare_llmfit.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'licenses').mkdir()
    (tmp_path / 'licenses/llmfit-MIT.txt').write_text('Fictional license fixture')
    archive = io.BytesIO()
    with ZipFile(archive, 'w') as bundle:
        bundle.writestr('release/llmfit.exe', b'MZfictional')
    payload = archive.getvalue()
    manifest = {'url': 'https://downloads.example.test/helper.zip', 'sha256': 'wrong'}
    (tmp_path / 'config/llmfit.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(module, 'urlopen', lambda *a, **kw: io.BytesIO(payload))
    with pytest.raises(ValueError, match='checksum'):
        module.prepare(tmp_path)
    assert not (tmp_path / '.tools').exists()
    manifest['sha256'] = hashlib.sha256(payload).hexdigest()
    (tmp_path / 'config/llmfit.json').write_text(json.dumps(manifest))
    module.prepare(tmp_path)
    assert (tmp_path / '.tools/llmfit/llmfit.exe').read_bytes() == b'MZfictional'
