"""An artist's exact name is not enough to assign a genre to a namesake."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class ReleaseGroupsTest(unittest.TestCase):
    def client(self, responses):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        client = MusicBrainz(Path(temp.name), "tests@example.invalid")
        client.get = MagicMock(side_effect=responses)
        return client

    def test_all_pages_of_an_unambiguous_artist(self):
        artist = {"artists": [{"id": "a", "name": "Band", "score": 100}]}
        client = self.client([artist, {"release-group-count": 3, "release-groups": [{"title": "One"}, {"title": "Two"}]}, {"release-group-count": 3, "release-groups": [{"title": "Three"}]}])
        self.assertEqual([g["title"] for g in client.release_groups("Band")], ["One", "Two", "Three"])
        self.assertEqual(client.get.call_args.kwargs["offset"], 100)

    def test_ambiguous_artist_is_not_guessed(self):
        same = [{"id": "a", "name": "Band", "score": 100}, {"id": "b", "name": "Band", "score": 100}]
        client = self.client([{"artists": same}])
        self.assertIsNone(client.release_groups("Band"))
        self.assertEqual(client.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
