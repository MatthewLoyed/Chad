from src.core.discovery.feed_preview import compact_preview, source_image


def test_preview_keeps_at_most_three_source_sentences():
    assert compact_preview('First fact. Second fact. Third fact. Fourth fact.') == 'First fact. Second fact. Third fact.'
    assert compact_preview('One supported sentence.') == 'One supported sentence.'


def test_source_image_uses_source_metadata_and_rejects_unsafe_urls():
    assert source_image('<meta property="og:image" content="/preview.png">', 'https://example.com/post') == 'https://example.com/preview.png'
    assert source_image('<meta name="twitter:image" content="https://example.com/twitter.png">', 'https://example.com') == 'https://example.com/twitter.png'
    assert source_image('<img src="javascript:alert(1)">', 'https://example.com') is None
    assert source_image('', 'https://example.com') is None
