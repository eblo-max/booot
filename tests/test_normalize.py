from src.domain.normalize import (
    normalize_emails,
    normalize_inn,
    normalize_ogrn,
    normalize_okved,
    normalize_phones,
    normalize_website,
    okved_matches,
)


class TestInn:
    def test_valid_10_digit(self):
        assert normalize_inn("7707083893") == "7707083893"

    def test_valid_12_digit(self):
        assert normalize_inn("500100732259") == "500100732259"

    def test_strips_separators(self):
        assert normalize_inn(" 7707-083-893 ") == "7707083893"

    def test_rejects_bad_checksum(self):
        assert normalize_inn("7707083894") is None

    def test_rejects_wrong_length(self):
        assert normalize_inn("12345") is None
        assert normalize_inn("") is None
        assert normalize_inn(None) is None


class TestOgrn:
    def test_valid_13_digit(self):
        assert normalize_ogrn("1027700132195") == "1027700132195"

    def test_rejects_bad_checksum(self):
        assert normalize_ogrn("1027700132196") is None

    def test_rejects_wrong_length(self):
        assert normalize_ogrn("102770013219") is None


class TestOkved:
    def test_normalizes_comma(self):
        assert normalize_okved("41,20") == "41.20"

    def test_extracts_from_description(self):
        assert normalize_okved("41.20 Строительство жилых зданий") == "41.20"

    def test_prefix_match_by_segment(self):
        assert okved_matches("41.20", ["41.20"])
        assert okved_matches("41.20", ["41.2"])
        assert okved_matches("41.20.1", ["41.20"])
        assert okved_matches("41.20", ["41"])

    def test_group_covers_subgroup(self):
        assert okved_matches("41.21", ["41.2"])

    def test_does_not_match_sibling(self):
        assert not okved_matches("41.21", ["41.20"])
        assert not okved_matches("43.11", ["41.20"])
        assert not okved_matches("43.12", ["43.11"])

    def test_pattern_more_specific_than_code(self):
        assert not okved_matches("41.2", ["41.20"])

    def test_empty_patterns_match_all(self):
        assert okved_matches("41.20", [])

    def test_missing_code_does_not_match(self):
        assert not okved_matches(None, ["41.20"])


class TestContacts:
    def test_phone_formats(self):
        assert normalize_phones(["8 (495) 123-45-67"]) == ["+74951234567"]
        assert normalize_phones(["+7 495 123 45 67"]) == ["+74951234567"]
        assert normalize_phones(["4951234567"]) == ["+74951234567"]

    def test_phone_dedup(self):
        assert normalize_phones(["84951234567", "+74951234567"]) == ["+74951234567"]

    def test_phone_rejects_garbage(self):
        assert normalize_phones(["123", "не указан"]) == []

    def test_phone_splits_string(self):
        assert len(normalize_phones("84951234567, 84951234568")) == 2

    def test_emails(self):
        assert normalize_emails("Info@Example.RU; bad@@x") == ["info@example.ru"]

    def test_website(self):
        assert normalize_website("https://www.Example.ru/") == "example.ru"
