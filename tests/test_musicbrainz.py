"""An artist's exact name is not enough to assign a genre to a namesake."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from musicbrainz_client import MusicBrainz


class GenreIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = MusicBrainz(Path(self.temp.name), "unit-tests@example.invalid")
        self.artist = {"artists": [{"id": "artist", "name": "Thorns", "score": "100"}]}
        self.group = {"id": "group", "title": "60 Cycle Hum", "score": "100", "artist-credit": [{"artist": {"id": "artist"}}]}

    def test_ambiguous_album_never_uses_a_genre_from_the_artist_name_alone(self):
        second = {**self.group, "id": "other-group"}
        with patch.object(self.client, "get", side_effect=[self.artist, {"release-groups": [self.group, second]}]) as get:
            result = self.client.genre("Thorns", "60 Cycle Hum")
        self.assertNotIn("genero", result)
        self.assertIn("ambiguous", result["revisao"])
        self.assertEqual(get.call_count, 2)

    def test_confirmed_album_genre_has_priority_without_fetching_artist_genres(self):
        with patch.object(self.client, "get", side_effect=[self.artist, {"release-groups": [self.group]}, {"id": "group", "genres": [{"name": "thrash metal", "count": 3}]}]) as get:
            result = self.client.genre("Thorns", "60 Cycle Hum")
        self.assertEqual(result["genero"], "Thrash Metal")
        self.assertEqual(result["fonte"], "https://musicbrainz.org/release-group/group")
        self.assertEqual(get.call_count, 3)

    def test_artist_fallback_requires_a_confirmed_album_without_its_own_genre(self):
        with patch.object(self.client, "get", side_effect=[self.artist, {"release-groups": [self.group]}, {"id": "group", "genres": []}, {"genres": [{"name": "thrash metal", "count": 3}]}]) as get:
            result = self.client.genre("Thorns", "60 Cycle Hum")
        self.assertEqual(result["genero"], "Thrash Metal")
        self.assertEqual(result["fonte"], "https://musicbrainz.org/artist/artist")
        self.assertIn("matching an album", result["criterio"])
        self.assertEqual(get.call_count, 4)


if __name__ == "__main__":
    unittest.main()
