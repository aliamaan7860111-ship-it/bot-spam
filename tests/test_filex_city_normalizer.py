import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

import unittest
from filex_city_normalizer import normalize_city

class TestCityNormalizer(unittest.TestCase):
    # Direct alias hits
    def test_dubai_simple(self):
        self.assertEqual(normalize_city("Apt 916 Binghatti Views Silicon Oasis Dubai"), "Dubai")

    def test_abu_dhabi(self):
        self.assertEqual(normalize_city("Villa 21 Asharej Abu Dhabi"), "Abu Dhabi")

    def test_sharjah(self):
        self.assertEqual(normalize_city("Al-Qurain 2nd Street, Villa No. 10 Sharjah"), "Sharjah")

    def test_ajman(self):
        self.assertEqual(normalize_city("Imam Al-Shafii Street, Al Hamidiyah, Ajman"), "Ajman")

    def test_al_ain(self):
        self.assertEqual(normalize_city("Villa 44 Shabiyat Khalifa Al Ain"), "Al Ain")

    # Filex spelling normalization
    def test_fujairah_maps_to_filex_fujeriah(self):
        self.assertEqual(normalize_city("Mirbah Fujairah 458/5"), "Fujeriah")

    def test_umm_al_quwain_maps_to_filex_um_al_qwain(self):
        self.assertEqual(normalize_city("Some place Umm Al Quwain"), "Um Al Qwain")

    def test_ras_al_khaimah_hyphen(self):
        self.assertEqual(normalize_city("Al-Khararan Villa Ras al-Khaimah"), "Ras Al Khaimah")

    # Typo / fuzzy
    def test_typo_dubaai(self):
        self.assertEqual(normalize_city("Pantheon Elysee 1 jvc dubaai"), "Dubai")

    def test_typo_abudhabi_no_space(self):
        self.assertEqual(normalize_city("802 argana building navygate abudhabi"), "Abu Dhabi")

    # Tiebreaker: first city wins
    def test_two_cities_picks_first(self):
        self.assertEqual(normalize_city("Mirbah Fujairah Abu Dhabi"), "Fujeriah")

    # No match
    def test_no_city_returns_none(self):
        self.assertIsNone(normalize_city("Random building street villa"))

    def test_empty_address(self):
        self.assertIsNone(normalize_city(""))

if __name__ == "__main__":
    unittest.main()
