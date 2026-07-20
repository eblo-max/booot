from src.domain.tax_status import TaxStatus, describe, resolve_tax_status


class TestResolve:
    def test_no_data_is_unknown_not_osno(self):
        """Главное правило: нет данных != нет спецрежима."""
        assert resolve_tax_status(registry_checked=False) == TaxStatus.UNKNOWN

    def test_checked_and_empty_is_probable_osno(self):
        assert resolve_tax_status(registry_checked=True, found_regimes=[]) == TaxStatus.OSNO_PROBABLE

    def test_found_regime_is_special(self):
        assert resolve_tax_status(registry_checked=True, found_regimes=["УСН"]) == TaxStatus.SPECIAL

    def test_explicit_source_confirms_osno(self):
        assert resolve_tax_status(registry_checked=True, source_states_osno=True) == TaxStatus.OSNO_CONFIRMED

    def test_regimes_without_check_still_special(self):
        assert resolve_tax_status(registry_checked=False, found_regimes=["ЕСХН"]) == TaxStatus.SPECIAL


class TestDescribe:
    def test_probable_never_claims_confirmed(self):
        text = describe(TaxStatus.OSNO_PROBABLE)
        assert text == "Вероятная ОСНО — специальные режимы не обнаружены"
        assert "подтверждена" not in text

    def test_confirmed_mentions_source(self):
        assert describe(TaxStatus.OSNO_CONFIRMED, source="Checko") == "ОСНО подтверждена (источник: Checko)"

    def test_unknown(self):
        assert describe(TaxStatus.UNKNOWN) == "Налоговый режим неизвестен"

    def test_special_lists_regimes(self):
        assert "УСН" in describe(TaxStatus.SPECIAL, regimes=["УСН"])
