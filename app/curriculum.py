"""The course catalogue: areas -> topics, and levels -> modules -> lessons.

One source of truth for everything the learner model is organised by
(taxonomy v2, decided 2026-09-26 - «делать нужно чисто и хорошо»):

  * a *topic* is one thing a lesson teaches ("Present Perfect и Past Simple",
    "Вежливые просьбы"). Claude tags every mistake with exactly one topic key,
    the item bank, attempts, AI sets and progress.json are keyed by it;
  * an *area* groups topics ("Времена глагола", "Предлоги") - the coarse level
    that statistics and «Прогресс» roll topics up to;
  * the *roadmap* is a fixed A2 -> C1 course: levels -> modules -> lessons.
    A lesson teaches exactly one topic and its id IS that topic key, so a
    lesson's progress and a topic's mistakes are the same records. Topics with
    no lesson are delivery (fillers, restarts - trained by spoken drills) and
    the "other" catch-all.

This file is authored by hand and never generated: the model only ever writes
a lesson's *content* (theory, exercises), on a click. Keys are stable -
changing or removing one is a migration of every file keyed by topics, with
schema_version bumps in analyzer.py, progress_store.py and learner_store.py.
Adding a topic is cheap but still bumps those versions, so derived caches and
the analysis prompt pick it up.

The descriptions are what Claude reads when it picks a topic, so each one says
what kind of *mistake* belongs there, in Russian like the rest of the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Final, List, Optional, Tuple


@dataclass(frozen=True)
class Area:
    key: str
    label: str


@dataclass(frozen=True)
class Topic:
    key: str
    area: str
    label: str
    description: str
    # Free online references (title, url) - no textbooks (decided 2026-09-19).
    resources: Tuple[Tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Level:
    key: str
    label: str


@dataclass(frozen=True)
class Module:
    key: str
    level: str
    title: str
    lessons: Tuple[str, ...]  # topic keys, in course order


# --------------------------------------------------------------------- areas
DELIVERY_AREA: Final[str] = "delivery"
OTHER_AREA: Final[str] = "other"
OTHER_TOPIC: Final[str] = "other"

AREAS: Final[Tuple[Area, ...]] = (
    Area("tenses", "Времена глагола"),
    Area("modals", "Модальные глаголы"),
    Area("conditionals", "Условные предложения"),
    Area("verb_patterns", "Глагольные конструкции"),
    Area("nouns_articles", "Артикли и существительные"),
    Area("adjectives_adverbs", "Прилагательные и наречия"),
    Area("prepositions", "Предлоги"),
    Area("sentence", "Строение предложения"),
    Area("word_choice", "Выбор слов"),
    Area("phrasal_collocations", "Фразовые глаголы и сочетания"),
    Area("word_formation", "Словообразование"),
    Area("functional", "Общение на английском"),
    Area("register", "Естественность и стиль"),
    Area(DELIVERY_AREA, "Беглость речи"),
    Area(OTHER_AREA, "Прочее"),
)

# -------------------------------------------------------------------- topics
_CAMBRIDGE: Final[str] = "https://dictionary.cambridge.org/grammar/british-grammar/"
_TENSES_LINK = (("Cambridge Grammar: Tenses and time", _CAMBRIDGE + "tenses-and-time"),)
_CONDITIONALS_LINK = (
    ("Cambridge Grammar: Conditionals and wishes", _CAMBRIDGE + "conditionals-and-wishes"),
)
_ARTICLES_LINK = (("Cambridge Grammar: A/an and the", _CAMBRIDGE + "a-an-and-the"),)
_PREPOSITIONS_LINK = (("Cambridge Grammar: Prepositions", _CAMBRIDGE + "prepositions"),)
_WORD_ORDER_LINK = (("Cambridge Grammar: Word order", _CAMBRIDGE + "word-order-and-focus"),)
_AGREEMENT_LINK = (
    ("Cambridge Grammar: Subject-verb agreement", _CAMBRIDGE + "subject-verb-agreement"),
)
_RELATIVE_LINK = (("Cambridge Grammar: Relative clauses", _CAMBRIDGE + "relative-clauses"),)

TOPICS: Final[Tuple[Topic, ...]] = (
    # --- tenses
    Topic("present_simple_continuous", "tenses", "Present Simple и Present Continuous",
          "Present Simple вместо Continuous или наоборот; пропущенное -s в 3-м лице "
          "(he work); привычки против того, что происходит сейчас", _TENSES_LINK),
    Topic("past_simple", "tenses", "Past Simple",
          "настоящее время вместо прошедшего в рассказе о прошлом (yesterday I go); "
          "неверная форма неправильного глагола; did + прошедшая форма", _TENSES_LINK),
    Topic("future_forms", "tenses", "Будущее: will, going to, Present Continuous",
          "Present Simple вместо будущего (I call you later); will вместо going to "
          "для планов и наоборот", _TENSES_LINK),
    Topic("present_perfect", "tenses", "Present Perfect и Past Simple",
          "Past Simple вместо Present Perfect (результат, опыт, «уже / ещё не») или "
          "Present Perfect с точным временем в прошлом (I have done it yesterday)",
          _TENSES_LINK),
    Topic("present_perfect_continuous", "tenses", "Present Perfect Continuous: for / since",
          "настоящее время для действия, длящегося до сих пор (I work here for 3 years); "
          "путаница for / since", _TENSES_LINK),
    Topic("past_continuous", "tenses", "Past Continuous в рассказе",
          "неверное сочетание Past Continuous и Past Simple (фон и событие) в рассказе "
          "о прошлом", _TENSES_LINK),
    Topic("past_perfect", "tenses", "Past Perfect",
          "нет Past Perfect там, где нужно действие раньше другого прошлого (when I came, "
          "they already left)", _TENSES_LINK),
    Topic("used_to", "tenses", "used to / would / be used to",
          "путаница used to (раньше, привычка) и be / get used to (привыкнуть); "
          "Present Simple для прошлых привычек", _TENSES_LINK),
    Topic("stative_verbs", "tenses", "Глаголы состояния",
          "Continuous с глаголами состояния (I am knowing, I am wanting, it is "
          "depending)", _TENSES_LINK),
    Topic("future_continuous_perfect", "tenses", "Future Continuous и Future Perfect",
          "нет Future Perfect / Continuous там, где нужно «к тому времени уже» или "
          "«в этот момент будет идти»", _TENSES_LINK),
    # --- modals
    Topic("modals_ability_permission", "modals", "can / could / be able to; просьбы и разрешение",
          "ошибки с can / could / be able to (can + to, will can); неверная форма "
          "просьбы или разрешения"),
    Topic("modals_obligation", "modals", "must / have to / should / don't have to",
          "путаница must / have to / should; mustn't вместо don't have to; must + to"),
    Topic("modals_deduction", "modals", "Предположения: must / might / can't be",
          "нет модального глагола для догадки (maybe it is broken вместо it might be "
          "broken); путаница must / can't / might"),
    Topic("modals_past", "modals", "should have / could have / must have",
          "неверная форма модального в прошедшем (should do вместо should have done); "
          "сожаление и упрёк о прошлом"),
    # --- conditionals
    Topic("conditionals_real", "conditionals", "Условные 0 и 1: if + Present, will",
          "will в придаточном с if (if it will fail); неверные времена реального "
          "условия", _CONDITIONALS_LINK),
    Topic("conditionals_unreal", "conditionals", "Условные 2: if + Past, would",
          "нереальное условие в настоящем с неверными временами (if I have time, I would); "
          "would в придаточном с if", _CONDITIONALS_LINK),
    Topic("conditionals_past", "conditionals", "Условные 3 и смешанные",
          "нереальное условие о прошлом с неверными формами (if I knew, I would do it — "
          "о прошлом); смешанные условные", _CONDITIONALS_LINK),
    Topic("wishes", "conditionals", "wish / if only / I'd rather",
          "неверное время после wish / if only / I'd rather (I wish I have)",
          _CONDITIONALS_LINK),
    Topic("time_clauses", "conditionals", "Придаточные времени: when / as soon as / until",
          "will в придаточном времени (when it will be ready, as soon as I will finish)",
          _CONDITIONALS_LINK),
    # --- verb patterns
    Topic("gerund_infinitive", "verb_patterns", "Герундий или инфинитив",
          "неверная форма глагола после другого глагола или предлога (avoid to do, "
          "interested to learn, instead of do)"),
    Topic("verb_object_infinitive", "verb_patterns", "help / let / make / want someone (to) do",
          "ошибки в конструкции глагол + объект + инфинитив (want that you do, "
          "let me to do, help me with doing)"),
    Topic("passive", "verb_patterns", "Страдательный залог",
          "нет пассива там, где нужен (the bug fixed yesterday), или неверная форма "
          "be + V3"),
    Topic("passive_advanced", "verb_patterns", "Сложный пассив и have something done",
          "ошибки в сложных формах пассива, it is said that / is expected to, "
          "have / get something done"),
    Topic("reported_speech", "verb_patterns", "Косвенная речь",
          "ошибки в косвенной речи: согласование времён, порядок слов в косвенном "
          "вопросе после said / asked, say / tell"),
    Topic("reporting_verbs", "verb_patterns", "suggest / recommend / insist и другие",
          "неверная конструкция после глаголов передачи речи (suggest you to do, "
          "recommend to use, explain me)"),
    # --- nouns and articles
    Topic("articles_basic", "nouns_articles", "Артикли a / an / the",
          "пропущенный, лишний или неверно выбранный артикль в обычных случаях "
          "(первое упоминание, единственный в своём роде, профессия)", _ARTICLES_LINK),
    Topic("articles_advanced", "nouns_articles", "Артикли: обобщения и устойчивые случаи",
          "артикль в обобщениях (the people are lazy), с абстрактными понятиями, "
          "в устойчивых выражениях (at the work, go to the bed)", _ARTICLES_LINK),
    Topic("countable_uncountable", "nouns_articles", "Исчисляемые и неисчисляемые",
          "множественное число неисчисляемых (informations, advices, feedbacks); "
          "much / many, few / little, a lot of"),
    Topic("quantifiers", "nouns_articles", "some / any / no, each / every, all / both",
          "ошибки с кванторами: some / any, every / each / all, either / neither, "
          "most / most of"),
    Topic("pronouns_possessives", "nouns_articles", "Местоимения и притяжательные",
          "неверное местоимение или притяжательная форма (him vs his, it's / its, "
          "the colleague of my, пропущенное it как подлежащее)"),
    # --- adjectives and adverbs
    Topic("comparatives_superlatives", "adjectives_adverbs", "Сравнение прилагательных",
          "ошибки в степенях сравнения (more better, the most fast, as ... than)"),
    Topic("adverbs", "adjectives_adverbs", "Наречия: -ly, частотность, место",
          "прилагательное вместо наречия (works good), неверное место наречия "
          "частотности или степени"),
    Topic("adjective_ing_ed", "adjectives_adverbs", "boring / bored: -ing и -ed",
          "путаница прилагательных на -ing и -ed (I am boring вместо I am bored)"),
    Topic("so_such_too_enough", "adjectives_adverbs", "so / such / too / enough",
          "ошибки с so / such / too / enough (such good, enough good, too much "
          "difficult)"),
    # --- prepositions
    Topic("prepositions_time_place", "prepositions", "Предлоги времени и места",
          "неверный или пропущенный предлог времени, места или направления (in Monday, "
          "at the Moscow, arrive to)", _PREPOSITIONS_LINK),
    Topic("dependent_prepositions", "prepositions", "Предлоги после глаголов и прилагательных",
          "неверный предлог после глагола, прилагательного или существительного "
          "(depend from, good in, discuss about, reason of)", _PREPOSITIONS_LINK),
    Topic("prepositional_phrases", "prepositions", "Предлоги в устойчивых выражениях",
          "неверный предлог в устойчивом выражении (by accident, on purpose, in charge "
          "of, on time / in time)", _PREPOSITIONS_LINK),
    # --- sentence
    Topic("word_order", "sentence", "Порядок слов",
          "неверный порядок слов в утверждении: подлежащее, сказуемое, дополнение, "
          "обстоятельства", _WORD_ORDER_LINK),
    Topic("questions", "sentence", "Вопросы",
          "ошибки в прямом вопросе: нет вспомогательного глагола или инверсии "
          "(you can help?, why you did it?)", _WORD_ORDER_LINK),
    Topic("indirect_questions", "sentence", "Косвенные вопросы",
          "прямой порядок слов не соблюдён в косвенном вопросе (do you know where is "
          "it?, I don't know what should I do)", _WORD_ORDER_LINK),
    Topic("negation", "sentence", "Отрицание",
          "двойное отрицание (I don't know nothing), неверное отрицание с "
          "any / no / nobody"),
    Topic("subject_verb_agreement", "sentence", "Согласование подлежащего и сказуемого",
          "рассогласование числа или лица между подлежащим и глаголом (the team "
          "are / people is / he don't)", _AGREEMENT_LINK),
    Topic("there_is_it_is", "sentence", "there is / there are и it is",
          "нет there is / there are или подлежащего it (in the project is a problem, "
          "is important to, it was many people)"),
    Topic("linking_words", "sentence", "Связки: although, however, because of, so that",
          "неверная связка или её употребление (although ... but, because of + "
          "предложение, however в середине без запятых)"),
    Topic("relative_clauses", "sentence", "Относительные придаточные: who / which / that",
          "ошибки в придаточных с who / which / that / where (the man which, the "
          "project what we did, лишнее местоимение)", _RELATIVE_LINK),
    Topic("sentence_structure", "sentence", "Структура длинного предложения",
          "незаконченные, рубленые или запутанные предложения: мысль брошена, "
          "фразы нанизаны без связи, предложение начато заново"),
    Topic("participle_clauses", "sentence", "Причастные обороты",
          "ошибки в причастных оборотах (Having finished..., Working as...), "
          "«висящее» причастие"),
    Topic("emphasis_inversion", "sentence", "Эмфаза и инверсия",
          "ошибки в выделительных конструкциях (What I need is..., It was ... who) "
          "и инверсии (Not only did..., Rarely have...)"),
    # --- word choice
    Topic("confusable_words", "word_choice", "Путаемые слова",
          "путаница близких слов: say / tell, lend / borrow, employer / employee, "
          "listen / hear, other / another, win / earn"),
    Topic("false_friends", "word_choice", "Ложные друзья и кальки",
          "ложный друг переводчика или дословная калька с русского (actual в смысле "
          "«актуальный», make a photo, on the photo, explain me)"),
    Topic("lexical_precision", "word_choice", "Точность выбора слова",
          "слово понятное, но неточное или слишком общее для смысла (do a solution, "
          "big problem вместо serious / major, very important everywhere)"),
    # --- phrasal verbs and collocations
    Topic("phrasal_verbs_basic", "phrasal_collocations", "Частые фразовые глаголы",
          "неверный фразовый глагол или частица (find out, set up, come up with, "
          "turn out, look for), или нет фразового глагола там, где он естественен"),
    Topic("phrasal_verbs_work", "phrasal_collocations", "Фразовые глаголы для работы",
          "ошибки в рабочих фразовых глаголах (roll out, follow up, look into, "
          "carry out, figure out, sort out)"),
    Topic("collocations_basic", "phrasal_collocations", "Устойчивые сочетания",
          "неестественное сочетание слов (do a decision, make a break, say a "
          "presentation, strong rain) - make / do / take / have / give"),
    Topic("collocations_work", "phrasal_collocations", "Рабочие коллокации",
          "неестественное сочетание в рабочем контексте (meet a deadline, raise an "
          "issue, address a problem, reach an agreement)"),
    Topic("idioms", "phrasal_collocations", "Идиомы и разговорные выражения",
          "неверно употреблённая идиома или разговорное выражение, искажённая "
          "устойчивая фраза"),
    # --- word formation
    Topic("word_formation_basic", "word_formation", "Суффиксы: -tion, -ment, -er, -ful, -less",
          "неверная часть речи (успешный → success вместо successful, decide вместо "
          "decision), ошибка в суффиксе"),
    Topic("word_formation_advanced", "word_formation", "Приставки и производные слова",
          "ошибки в приставках и производных (un- / in- / dis- / mis- / over- / "
          "under-, -ise, -ity)"),
    # --- functional English
    Topic("small_talk", "functional", "Small talk",
          "неуклюжее или неестественное начало разговора, ответ на How are you, "
          "поддержание лёгкой беседы"),
    Topic("polite_requests", "functional", "Вежливые просьбы и предложения",
          "слишком прямая или грубая просьба / предложение (give me, you must send), "
          "нет Could you / Would you mind / Shall I"),
    Topic("opinions", "functional", "Мнение, согласие и несогласие",
          "неестественное выражение мнения, согласия или несогласия (I am agree, "
          "according to me, in my opinion I think)"),
    Topic("meetings", "functional", "Встречи и созвоны",
          "неестественные фразы на встрече: начать, перебить, уточнить, подвести "
          "итог, договориться о следующих шагах"),
    Topic("emails", "functional", "Рабочие письма и сообщения",
          "неверный тон или формулы в письме и рабочем сообщении (приветствие, "
          "просьба, напоминание, завершение)"),
    Topic("storytelling", "functional", "Рассказ о событии",
          "рассказ без последовательности и связок: непонятно, что было раньше, "
          "что потом и чем закончилось"),
    Topic("describing_problems", "functional", "Описать проблему и решение",
          "неясное описание проблемы, бага или инцидента, его причины и "
          "предлагаемого решения"),
    Topic("disagreeing_politely", "functional", "Вежливое несогласие и смягчение",
          "слишком резкое несогласие, критика или отказ (you are wrong, it is bad "
          "idea) без смягчения"),
    Topic("presentations", "functional", "Презентации и объяснение технического",
          "неструктурированное объяснение: нет вступления, переходов, выводов; "
          "сложно объяснено техническое"),
    Topic("interviews", "functional", "Собеседование",
          "неудачные формулировки на собеседовании: рассказ о себе, опыте, "
          "достижениях, сильных и слабых сторонах"),
    Topic("negotiating", "functional", "Переговоры и аргументация",
          "неубедительная или неестественная аргументация, уступки, условия, "
          "компромисс"),
    # --- register and naturalness
    Topic("register_naturalness", "register", "Естественность речи",
          "грамматически верно, но так не говорят носители: неестественная, "
          "книжная или переведённая с русского фраза"),
    Topic("formal_informal", "register", "Формальный и неформальный стиль",
          "стиль не подходит ситуации: слишком официально в разговоре или "
          "слишком фамильярно в деловом контексте"),
    Topic("hedging", "register", "Смягчение и осторожные формулировки",
          "слишком категорично там, где уместна осторожность (it is, always, never "
          "вместо it seems, tends to, I'd say)"),
    Topic("conciseness", "register", "Краткость",
          "многословие: лишние слова, повторы смысла, длинный оборот вместо "
          "короткого слова"),
    # --- delivery (spoken drills train these, never a lesson)
    Topic("filler_words_fluency", DELIVERY_AREA, "Слова-паразиты и беглость речи",
          "частые слова-паразиты, паузы и запинки, мешающие беглости"),
    Topic("repetition_self_correction", DELIVERY_AREA, "Повторы и самокоррекции",
          "повторение слов и фраз, частые самокоррекции по ходу речи"),
    Topic(OTHER_TOPIC, OTHER_AREA, "Прочее",
          "любая другая проблема, не подходящая ни под одну тему выше"),
)

# -------------------------------------------------------------------- roadmap
LEVELS: Final[Tuple[Level, ...]] = (
    Level("a2", "A2 · Элементарный"),
    Level("b1", "B1 · Средний"),
    Level("b2", "B2 · Выше среднего"),
    Level("c1", "C1 · Продвинутый"),
)

MODULES: Final[Tuple[Module, ...]] = (
    Module("a2_verbs", "a2", "Глагол: настоящее, прошлое, будущее", (
        "present_simple_continuous", "past_simple", "future_forms",
        "modals_ability_permission", "modals_obligation", "conditionals_real",
    )),
    Module("a2_sentence", "a2", "Строим предложение", (
        "word_order", "questions", "negation", "subject_verb_agreement", "there_is_it_is",
    )),
    Module("a2_nouns", "a2", "Существительные, описания, предлоги", (
        "articles_basic", "countable_uncountable", "pronouns_possessives",
        "comparatives_superlatives", "adverbs", "prepositions_time_place",
    )),
    Module("a2_communication", "a2", "Первые разговоры", (
        "confusable_words", "small_talk", "polite_requests",
    )),
    Module("b1_tenses", "b1", "Времена: прошлое и настоящее вместе", (
        "present_perfect", "present_perfect_continuous", "past_continuous", "past_perfect",
        "used_to", "stative_verbs",
    )),
    Module("b1_verbs", "b1", "Условия и глагольные конструкции", (
        "conditionals_unreal", "time_clauses", "gerund_infinitive", "verb_object_infinitive",
        "passive",
    )),
    Module("b1_sentence", "b1", "Сложное предложение", (
        "relative_clauses", "linking_words", "indirect_questions", "sentence_structure",
    )),
    Module("b1_details", "b1", "Точность в деталях", (
        "quantifiers", "adjective_ing_ed", "so_such_too_enough", "dependent_prepositions",
    )),
    Module("b1_vocabulary", "b1", "Слова и сочетания", (
        "false_friends", "phrasal_verbs_basic", "collocations_basic", "word_formation_basic",
    )),
    Module("b1_work", "b1", "Работа и общение", (
        "opinions", "meetings", "emails", "storytelling", "describing_problems",
    )),
    Module("b2_grammar", "b2", "Время, предположения, нереальное", (
        "future_continuous_perfect", "modals_deduction", "modals_past", "conditionals_past",
        "wishes",
    )),
    Module("b2_structures", "b2", "Пассив, косвенная речь, артикли", (
        "passive_advanced", "reported_speech", "articles_advanced", "prepositional_phrases",
    )),
    Module("b2_vocabulary", "b2", "Рабочая лексика", (
        "lexical_precision", "phrasal_verbs_work", "collocations_work",
        "word_formation_advanced",
    )),
    Module("b2_communication", "b2", "Звучать естественно", (
        "register_naturalness", "formal_informal", "disagreeing_politely", "presentations",
        "interviews",
    )),
    Module("c1_grammar", "c1", "Продвинутые конструкции", (
        "participle_clauses", "emphasis_inversion", "reporting_verbs",
    )),
    Module("c1_style", "c1", "Стиль и точность", (
        "hedging", "conciseness", "idioms", "negotiating",
    )),
)

# ------------------------------------------------------------------- indexes
AREA_BY_KEY: Final[Dict[str, Area]] = {area.key: area for area in AREAS}
TOPIC_BY_KEY: Final[Dict[str, Topic]] = {topic.key: topic for topic in TOPICS}
TOPIC_KEYS: Final[Tuple[str, ...]] = tuple(TOPIC_BY_KEY)
LEVEL_BY_KEY: Final[Dict[str, Level]] = {level.key: level for level in LEVELS}
MODULE_BY_KEY: Final[Dict[str, Module]] = {module.key: module for module in MODULES}
# Lesson id (= topic key) -> its module; also the course order.
MODULE_OF_LESSON: Final[Dict[str, Module]] = {
    lesson: module for module in MODULES for lesson in module.lessons
}
LESSON_IDS: Final[Tuple[str, ...]] = tuple(MODULE_OF_LESSON)

# Delivery topics are about how something was said, not knowledge a card can
# test ("uh, I, I think" -> "I think"); spoken drills train them instead.
NON_RECALL_TOPICS: Final[frozenset] = frozenset(
    topic.key for topic in TOPICS if topic.area == DELIVERY_AREA
)


def _validate() -> None:
    """Fail at import, not at runtime, if the hand-written catalogue is inconsistent."""
    if len(TOPIC_BY_KEY) != len(TOPICS) or len(AREA_BY_KEY) != len(AREAS):
        raise ValueError("Duplicate area or topic key in the curriculum.")
    for topic in TOPICS:
        if topic.area not in AREA_BY_KEY:
            raise ValueError(f"Topic {topic.key!r} names an unknown area {topic.area!r}.")
    lessons = [lesson for module in MODULES for lesson in module.lessons]
    if len(lessons) != len(set(lessons)):
        raise ValueError("A topic is taught by more than one lesson.")
    for module in MODULES:
        if module.level not in LEVEL_BY_KEY:
            raise ValueError(f"Module {module.key!r} names an unknown level.")
        for lesson in module.lessons:
            topic = TOPIC_BY_KEY.get(lesson)
            if topic is None or topic.area in (DELIVERY_AREA, OTHER_AREA):
                raise ValueError(f"Lesson {lesson!r} is not a teachable topic.")


_validate()


# ------------------------------------------------------------------- helpers
def is_topic(key: Optional[str]) -> bool:
    return key in TOPIC_BY_KEY


def topic_label(key: Optional[str]) -> str:
    topic = TOPIC_BY_KEY.get(key or "")
    return topic.label if topic else (key or "")


def area_of(key: Optional[str]) -> str:
    """The area a topic belongs to; unknown keys (older files) count as "other"."""
    topic = TOPIC_BY_KEY.get(key or "")
    return topic.area if topic else OTHER_AREA


def level_of(key: Optional[str]) -> Optional[str]:
    """The roadmap level whose lesson teaches this topic; None for delivery / other."""
    module = MODULE_OF_LESSON.get(key or "")
    return module.level if module else None


def topic_info(key: str) -> Dict[str, object]:
    """A topic as the screens show it: label, area, level, description, links."""
    topic = TOPIC_BY_KEY.get(key)
    area = AREA_BY_KEY[area_of(key)]
    module = MODULE_OF_LESSON.get(key)
    return {
        "key": key,
        "label": topic.label if topic else key,
        "description": topic.description if topic else "",
        "area": area.key,
        "area_label": area.label,
        "level": module.level if module else None,
        "module": module.key if module else None,
        "resources": [
            {"title": title, "url": url} for title, url in (topic.resources if topic else ())
        ],
    }


def taxonomy_prompt_lines(indent: str = "") -> str:
    """The closed topic list for a prompt, grouped under area headings."""
    lines: List[str] = []
    for area in AREAS:
        lines.append(f"{indent}{area.label}:")
        for topic in TOPICS:
            if topic.area == area.key:
                lines.append(f'{indent}  - "{topic.key}": {topic.description}')
    return "\n".join(lines)


def catalogue() -> Dict[str, object]:
    """The whole catalogue as JSON for the frontend (GET /api/curriculum)."""
    return {
        "areas": [{"key": a.key, "label": a.label} for a in AREAS],
        "topics": [topic_info(t.key) for t in TOPICS],
        "levels": [
            {
                "key": level.key,
                "label": level.label,
                "modules": [
                    {"key": m.key, "title": m.title, "lessons": list(m.lessons)}
                    for m in MODULES
                    if m.level == level.key
                ],
            }
            for level in LEVELS
        ],
    }
