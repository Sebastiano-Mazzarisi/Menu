import unittest
from datetime import datetime, timedelta

from Notizie import News, interest_score, is_obsolete, merge_duplicates, same_story


class NewsSelectionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 4, 10, 0)

    def news(self, title, source="Informatissimo", town="Putignano", hours=2, summary=""):
        return News(
            title=title,
            link=f"https://example.test/{source}/{abs(hash(title))}",
            published=self.now - timedelta(hours=hours),
            source=source,
            town=town,
            summary=summary,
        )

    def test_recognizes_different_titles_about_same_story(self):
        first = self.news("Putignano, arresto per la morte di Giuseppe Petramala")
        second = self.news("Un arresto per la morte di Giuseppe Petramala", source="Faxonline")
        self.assertTrue(same_story(first, second))

    def test_does_not_merge_unrelated_local_news(self):
        first = self.news("Nuovo asilo nido comunale, iniziati i lavori")
        second = self.news("Carnevale 2027, annunciate le date delle sfilate")
        self.assertFalse(same_story(first, second))

    def test_merge_keeps_one_item_and_lists_confirming_sources(self):
        first = self.news("Putignano, arresto per la morte di Giuseppe Petramala")
        second = self.news("Un arresto per la morte di Giuseppe Petramala", source="Faxonline")
        merged = merge_duplicates([first, second], self.now)
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0].duplicate_sources), {"Informatissimo", "Faxonline"})

    def test_putignano_and_public_safety_receive_more_weight(self):
        urgent = self.news("Allerta arancione e chiusura delle scuole a Putignano")
        generic = self.news("Mostra fotografica", town="Noci")
        self.assertGreater(interest_score(urgent, self.now), interest_score(generic, self.now))

    def test_old_weather_alert_is_obsolete(self):
        old_alert = self.news("Allerta arancione per temporali e vento", hours=36)
        old_bulletin = self.news("Protezione civile: situazione meteo prevista per il 2 novembre", hours=36)
        current_alert = self.news("Allerta gialla per rischio idrogeologico", hours=10)
        self.assertTrue(is_obsolete(old_alert, self.now))
        self.assertTrue(is_obsolete(old_bulletin, self.now))
        self.assertFalse(is_obsolete(current_alert, self.now))

    def test_old_non_weather_story_is_not_marked_obsolete(self):
        story = self.news("Approvato il nuovo piano urbanistico comunale", hours=60)
        self.assertFalse(is_obsolete(story, self.now))


if __name__ == "__main__":
    unittest.main()
