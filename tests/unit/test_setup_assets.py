from src.infrastructure.services import setup_assets as assets


def test_jobs_require_confirmation_and_do_not_start_by_reading_progress(monkeypatch):
    monkeypatch.setattr(assets.threading, 'Thread', lambda **kwargs: (_ for _ in ()).throw(AssertionError('unexpected job')))
    assert assets.start('download', {})['failure_class'] == 'bad_input'
    assert not assets.progress()['running']


def test_unknown_catalog_cannot_write_arbitrary_downloads(tmp_path):
    result = assets._download({'catalog_id': '../../untrusted', 'storage_path': str(tmp_path)})
    assert result['failure_class'] == 'bad_input'
    assert not list(tmp_path.iterdir())


def test_completed_exact_artifact_reused_without_network(monkeypatch, tmp_path):
    model = {'id': 'example', 'repo': 'Example/Test', 'filename': 'test.gguf', 'size_bytes': 4}
    monkeypatch.setattr(assets, 'catalog', lambda: [model])
    target = tmp_path / 'Example/Test/test.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'test')
    monkeypatch.setattr(assets.httpx, 'stream', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network')))
    assert assets._download({'catalog_id': 'example', 'storage_path': str(tmp_path)})['success']


def test_mismatched_existing_model_never_overwritten(monkeypatch, tmp_path):
    model = {'id': 'example', 'repo': 'Example/Test', 'filename': 'test.gguf', 'size_bytes': 4}
    monkeypatch.setattr(assets, 'catalog', lambda: [model])
    target = tmp_path / 'Example/Test/test.gguf'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'other-file')
    assert not assets._download({'catalog_id': 'example', 'storage_path': str(tmp_path)})['success']
    assert target.read_bytes() == b'other-file'


def test_embedding_download_includes_cross_repo_custom_code(monkeypatch):
    import huggingface_hub
    calls = []
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', lambda repo, **kwargs: calls.append((repo, kwargs)))
    assert assets._embedding()['success']
    assert [repo for repo, _ in calls] == ['nomic-ai/nomic-embed-text-v2-moe', 'nomic-ai/nomic-bert-2048']
    assert calls[1][1]['allow_patterns'] == ['*.py', 'config.json']


def test_fully_received_partial_is_finalized_without_another_request(monkeypatch, tmp_path):
    model = {'id': 'example', 'repo': 'Example/Test', 'filename': 'test.gguf', 'size_bytes': 4}
    monkeypatch.setattr(assets, 'catalog', lambda: [model])
    partial = tmp_path / 'Example/Test/test.gguf.part'
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b'test')
    assert assets._download({'catalog_id': 'example', 'storage_path': str(tmp_path)})['success']
    assert partial.with_suffix('').read_bytes() == b'test'
