from src.core.tts.sentence_accumulator import SentenceAccumulator


def test_feed_yields_completed_sentences_only():
    acc = SentenceAccumulator()
    assert acc.feed("Hello there") == []  # no boundary yet
    assert acc.feed(". How are") == ["Hello there."]
    assert acc.feed(" you? I'm ") == ["How are you?"]


def test_feed_across_many_small_deltas():
    acc = SentenceAccumulator()
    sentences = []
    for ch in "Hi! ", "This ", "is ", "chunked. ", "Last part":
        sentences.extend(acc.feed(ch))
    assert sentences == ["Hi!", "This is chunked."]
    assert acc.flush() == "Last part"


def test_flush_returns_none_when_buffer_empty():
    acc = SentenceAccumulator()
    acc.feed("Complete sentence. ")
    assert acc.flush() is None


def test_flush_clears_buffer():
    acc = SentenceAccumulator()
    acc.feed("partial")
    assert acc.flush() == "partial"
    assert acc.flush() is None
