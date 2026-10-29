import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from solution import count_vowels, most_common_word  # noqa: E402


def test_count_vowels():
    assert count_vowels("Hello World") == 3


def test_count_vowels_empty():
    assert count_vowels("") == 0


def test_most_common_word_ignores_case():
    assert most_common_word("the cat The dog") == "the"


def test_most_common_word_empty_and_tie():
    assert most_common_word("") is None
    assert most_common_word("b a") == "b"
