"""The hand-written course catalogue stays consistent (taxonomy v2)."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import curriculum  # noqa: E402

# Topic keys travel through the API as slugs (api._SLUG_PATTERN).
_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class CurriculumTests(unittest.TestCase):
    def test_every_key_is_a_slug(self) -> None:
        for key in curriculum.TOPIC_KEYS + tuple(a.key for a in curriculum.AREAS):
            self.assertRegex(key, _SLUG)

    def test_every_teachable_topic_has_exactly_one_lesson(self) -> None:
        untaught = {
            t.key for t in curriculum.TOPICS if t.key not in curriculum.MODULE_OF_LESSON
        }
        self.assertEqual(untaught, set(curriculum.NON_RECALL_TOPICS) | {curriculum.OTHER_TOPIC})
        self.assertEqual(len(curriculum.LESSON_IDS), len(set(curriculum.LESSON_IDS)))

    def test_every_area_has_topics_and_every_level_has_modules(self) -> None:
        used_areas = {t.area for t in curriculum.TOPICS}
        self.assertEqual(used_areas, set(curriculum.AREA_BY_KEY))
        used_levels = {m.level for m in curriculum.MODULES}
        self.assertEqual(used_levels, set(curriculum.LEVEL_BY_KEY))

    def test_course_order_follows_the_levels(self) -> None:
        order = [lvl.key for lvl in curriculum.LEVELS]
        levels = [curriculum.MODULE_OF_LESSON[lesson].level for lesson in curriculum.LESSON_IDS]
        self.assertEqual(levels, sorted(levels, key=order.index))

    def test_delivery_topics_are_the_non_recall_ones(self) -> None:
        self.assertEqual(
            set(curriculum.NON_RECALL_TOPICS),
            {"filler_words_fluency", "repetition_self_correction"},
        )

    def test_topic_info_and_unknown_keys(self) -> None:
        info = curriculum.topic_info("articles_basic")
        self.assertEqual((info["area"], info["level"], info["module"]),
                         ("nouns_articles", "a2", "a2_nouns"))
        self.assertTrue(info["resources"][0]["url"].startswith("https://"))
        unknown = curriculum.topic_info("verb_tense")  # a taxonomy v1 key
        self.assertEqual((unknown["label"], unknown["area"], unknown["level"]),
                         ("verb_tense", curriculum.OTHER_AREA, None))

    def test_prompt_lines_group_every_topic_under_its_area(self) -> None:
        text = curriculum.taxonomy_prompt_lines()
        for area in curriculum.AREAS:
            self.assertIn(f"{area.label}:", text)
        for key in curriculum.TOPIC_KEYS:
            self.assertEqual(text.count(f'"{key}"'), 1)

    def test_catalogue_nests_levels_modules_lessons(self) -> None:
        data = curriculum.catalogue()
        lessons = [
            lesson
            for level in data["levels"]
            for module in level["modules"]
            for lesson in module["lessons"]
        ]
        self.assertEqual(tuple(lessons), curriculum.LESSON_IDS)


if __name__ == "__main__":
    unittest.main()
