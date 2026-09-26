"""Contexts («уклон») for everything the model writes, and the speaking prompts.

Decided 2026-09-26 (Stage 8, R3): before *every* generation - an AI set, the
practice sentences of an analysis, speaking prompts - the learner picks the
situation the sentences should come from: IT / backend, meetings, travel,
their own «ремонт машины», or a one-off typed line. The default is the last one
used; the first default is IT / backend (the learner is a backend developer).
Theory does not follow the context.

In code a context is a *theme* ({"key", "label"}): "context" already names the
screen an attempt was made on (attempts.jsonl). Built-in themes live here and
carry hand-written speaking prompts ($0). The learner's own themes are stored
by app.theme_store; their prompts are written by Claude on a click. A one-off
theme has no key - just its label.

Pure data and helpers: nothing here reads files or calls a model.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, Final, List, Optional, Sequence, Tuple

Prompt = Tuple[str, str]  # (English question, Russian hint)


@dataclass(frozen=True)
class Theme:
    key: str
    label: str
    # What the model is told about the situations to write sentences in.
    description: str
    prompts: Tuple[Prompt, ...] = ()


MIXED: Final[str] = "mixed"
DEFAULT_THEME: Final[str] = "it_backend"
# A one-off or own theme label, as the learner types it.
LABEL_MAX_CHARS: Final[int] = 60

BUILTIN_THEMES: Final[Tuple[Theme, ...]] = (
    Theme(
        "it_backend", "IT / бэкенд",
        "a backend developer's work: services and APIs, databases, bugs and incidents, "
        "deployments, code review, tickets, tools, working with colleagues and managers",
        (
            ("Tell me about a project you worked on recently. What was your part in it?",
             "Недавний проект и ваша роль в нём"),
            ("What was the hardest bug or problem you solved at work? How did you find it?",
             "Самая сложная проблема на работе"),
            ("Explain what your company or team does to someone who is not in IT.",
             "Чем занимается ваша команда — простыми словами"),
            ("Tell me about a tool or technology you started using recently. "
             "Would you recommend it?", "Новый инструмент или технология"),
            ("Describe how a request travels through the system you work on, "
             "from the client to the database.", "Путь запроса через вашу систему"),
            ("Tell me about a production incident. What happened, and what did the team "
             "change afterwards?", "Инцидент в проде и выводы"),
            ("What does a good code review look like for you?", "Каким должно быть хорошее ревью"),
            ("Explain how you would onboard a new person on your team.",
             "Как ввести новичка в команду"),
        ),
    ),
    Theme(
        "meetings", "Созвоны и митинги",
        "calls and meetings at work: stand-ups, planning, demos, retrospectives, "
        "agreeing on next steps, asking for clarification, interrupting politely",
        (
            ("Describe a meeting that went badly. What would you do differently?",
             "Неудачная встреча"),
            ("Give a short stand-up update: what you did yesterday, what you will do today, "
             "and what blocks you.", "Апдейт на стендапе"),
            ("You disagree with a plan in a meeting. Explain your concerns politely.",
             "Вежливо не согласиться на встрече"),
            ("Summarise the last planning or demo meeting you had for someone who missed it.",
             "Пересказать встречу пропустившему"),
            ("What makes a meeting useful, and what makes it a waste of time?",
             "Полезная и бесполезная встреча"),
            ("Run a short retrospective: what went well in the last sprint, and what didn't?",
             "Ретроспектива спринта"),
            ("You need to postpone a deadline. Explain the reasons to your manager on a call.",
             "Перенести дедлайн"),
            ("Open a meeting: greet people, state the goal and the agenda.",
             "Открыть встречу"),
        ),
    ),
    Theme(
        "interview", "Собеседование",
        "job interviews: talking about experience, projects, strengths and weaknesses, "
        "salary and conditions, questions to the employer",
        (
            ("You are in a job interview. Introduce yourself and your experience.",
             "Собеседование: расскажите о себе"),
            ("Tell me about a mistake you made at work and what you learned from it.",
             "Ошибка на работе и вывод из неё"),
            ("Describe a disagreement with a manager or client and how it was resolved.",
             "Разногласие и как его решили"),
            ("What are your strengths and weaknesses as an engineer?",
             "Сильные и слабые стороны"),
            ("Why are you looking for a new job, and what do you expect from the next one?",
             "Почему ищете новую работу"),
            ("Tell me about the achievement you are most proud of.",
             "Главное достижение"),
            ("Where do you see yourself in three years?", "Где вы видите себя через три года"),
            ("What questions would you ask the company at the end of an interview, and why?",
             "Вопросы работодателю"),
        ),
    ),
    Theme(
        "travel", "Путешествия",
        "travelling: airports, hotels, public transport, asking for directions, "
        "sightseeing, problems on a trip",
        (
            ("Describe your last vacation or a trip you remember well.",
             "Последний отпуск или поездка"),
            ("Your flight has been cancelled. Explain the situation at the airline desk and "
             "ask for options.", "Отменили рейс"),
            ("Check in at a hotel and complain politely that the room is not what you booked.",
             "Заселение и жалоба на номер"),
            ("Tell a friend how to get from the airport to the city centre in your city.",
             "Как добраться из аэропорта"),
            ("Plan a weekend trip for a colleague visiting your city.",
             "Выходные для гостя вашего города"),
            ("What went wrong on a trip once, and how did you deal with it?",
             "Что пошло не так в поездке"),
            ("Do you prefer travelling alone or with others? Why?",
             "Одному или в компании"),
            ("Describe a place you would like to visit and what you would do there.",
             "Место, куда хочется поехать"),
        ),
    ),
    Theme(
        "shopping", "Покупки",
        "shopping: in shops and online, choosing, comparing, returns and refunds, "
        "delivery problems, talking to support",
        (
            ("Tell me about something you bought recently and how you chose it.",
             "Недавняя покупка и как выбирали"),
            ("You want to return an item that broke after a week. Talk to the shop assistant.",
             "Вернуть сломанную вещь"),
            ("Your online order is late. Call the support service and explain the problem.",
             "Заказ опаздывает"),
            ("Do you prefer shopping online or in shops? Why?", "Онлайн или в магазине"),
            ("Ask a shop assistant for advice on a laptop for work.",
             "Совет продавца по ноутбуку"),
            ("Describe the best and the worst purchase you have ever made.",
             "Лучшая и худшая покупка"),
            ("How do you decide whether something is worth its price?",
             "Стоит ли вещь своих денег"),
            ("Explain to a friend how to buy something safely on a second-hand website.",
             "Покупка с рук"),
        ),
    ),
    Theme(
        "restaurant", "Ресторан",
        "cafés and restaurants: booking a table, ordering, dietary needs, "
        "complaining about a dish, paying and tipping",
        (
            ("Book a table by phone for a team dinner of eight people.",
             "Забронировать стол для команды"),
            ("Order a meal and ask the waiter about ingredients because of an allergy.",
             "Заказ и аллергия"),
            ("Your dish is cold and not what you ordered. Talk to the waiter.",
             "Жалоба на блюдо"),
            ("Describe your favourite café or restaurant and why you like it.",
             "Любимое кафе"),
            ("Recommend a local dish to a foreign colleague and explain how it is made.",
             "Посоветовать местное блюдо"),
            ("Tell me about a memorable meal you had.", "Запомнившийся ужин"),
            ("Do you prefer cooking at home or eating out? Why?", "Готовить или есть вне дома"),
            ("Explain to a visitor how tipping works in your country.", "Чаевые в вашей стране"),
        ),
    ),
    Theme(
        "housing", "Жильё",
        "housing: renting, viewing a flat, talking to a landlord, repairs, neighbours, "
        "moving house",
        (
            ("Describe the place where you live and what you would change in it.",
             "Ваше жильё и что бы изменили"),
            ("You are viewing a flat to rent. Ask the landlord the important questions.",
             "Просмотр квартиры"),
            ("The heating in your flat has stopped working. Call the landlord.",
             "Сломалось отопление"),
            ("Your neighbours are too loud at night. Talk to them politely.",
             "Шумные соседи"),
            ("Tell me about a time you moved house. What was the hardest part?", "Переезд"),
            ("What makes a neighbourhood a good place to live?", "Хороший район для жизни"),
            ("Would you rather rent or buy a home? Explain your reasons.",
             "Снимать или покупать"),
            ("Describe your ideal home office.", "Идеальное рабочее место дома"),
        ),
    ),
    Theme(
        "health", "Здоровье",
        "health: seeing a doctor, describing symptoms, a pharmacy, sleep, sport and "
        "habits, work-life balance",
        (
            ("You feel ill. Describe your symptoms to a doctor.", "Симптомы у врача"),
            ("Ask a pharmacist for something for a cold and how to take it.", "В аптеке"),
            ("What do you do to stay healthy while working at a computer all day?",
             "Здоровье при сидячей работе"),
            ("Tell me about a habit you started or quit, and how it went.",
             "Привычка, которую начали или бросили"),
            ("How do you deal with stress and tiredness at work?", "Стресс и усталость"),
            ("Call the office and explain that you are sick and cannot work today.",
             "Сообщить, что заболели"),
            ("How much do you sleep, and what helps you sleep better?", "Сон"),
            ("Describe a time you were injured or ill while travelling.",
             "Болезнь или травма в поездке"),
        ),
    ),
    Theme(
        "small_talk", "Small talk",
        "light conversation: weather, weekends, plans, family, food, films, coffee-break "
        "chat with colleagues and strangers",
        (
            ("A colleague asks about your weekend. Tell them about it.", "Как прошли выходные"),
            ("You meet someone new at a conference. Start a conversation and keep it going.",
             "Знакомство на конференции"),
            ("What is a book, film or series you liked recently? Retell the idea.",
             "Книга, фильм или сериал"),
            ("What do you like about remote work, and what do you miss about the office?",
             "Удалёнка и офис"),
            ("Talk about the weather and the season where you live, and how it affects you.",
             "Погода и время года"),
            ("What are your plans for the coming holidays?", "Планы на праздники"),
            ("Tell a colleague about your city and what is worth seeing there.",
             "Ваш город"),
            ("What are your goals for the next year, at work and outside of it?",
             "Цели на год"),
        ),
    ),
    Theme(
        "hobbies_sport", "Хобби и спорт",
        "hobbies and sport: training, games, music, cooking, learning something new, "
        "clubs and competitions",
        (
            ("Tell me about your hobby and how you got into it.", "Ваше хобби"),
            ("How do you learn new things? Give an example from the last month.",
             "Как вы учитесь новому"),
            ("Describe your typical workout or the sport you do.", "Ваша тренировка"),
            ("Explain the rules of a game or sport you like to someone who has never played.",
             "Правила игры"),
            ("What hobby would you like to try, and why?", "Хобби, которое хотите попробовать"),
            ("How do you plan your week? What helps you find time for yourself?",
             "Как вы планируете неделю"),
            ("Tell me about a competition, match or event you took part in or watched.",
             "Соревнование или матч"),
            ("Recommend a podcast, channel or game to a friend and explain why.",
             "Что посоветуете"),
        ),
    ),
    Theme(
        "news", "Новости",
        "news and current events: technology, science, economy, society, the environment "
        "- explaining an event and giving an opinion",
        (
            ("Tell me about a piece of technology news that caught your attention recently.",
             "Новость из мира технологий"),
            ("How is AI changing the work of software engineers? Give your opinion.",
             "ИИ и работа разработчика"),
            ("Retell a news story you read this week and say what you think about it.",
             "Пересказать новость"),
            ("Where do you get your news, and how do you decide what to trust?",
             "Откуда вы берёте новости"),
            ("What change in your city or country over the last years do you notice most?",
             "Перемены вокруг"),
            ("Should people work four days a week? Give arguments for and against.",
             "Четырёхдневная рабочая неделя"),
            ("Describe an environmental problem and what could be done about it.",
             "Экологическая проблема"),
            ("What would you change in your current work process if you could?",
             "Что бы вы изменили в рабочем процессе"),
        ),
    ),
    Theme(
        MIXED, "Вперемешку",
        "a mix of situations - work and IT, meetings, travel, shopping, home, health, "
        "hobbies and small talk; vary them from sentence to sentence",
    ),
)

THEME_BY_KEY: Final[Dict[str, Theme]] = {theme.key: theme for theme in BUILTIN_THEMES}


def _validate() -> None:
    if len(THEME_BY_KEY) != len(BUILTIN_THEMES) or DEFAULT_THEME not in THEME_BY_KEY:
        raise ValueError("Duplicate or missing built-in theme.")
    for theme in BUILTIN_THEMES:
        if theme.key != MIXED and not theme.prompts:
            raise ValueError(f"Theme {theme.key!r} has no speaking prompts.")


_validate()


# ------------------------------------------------------------------- helpers
def is_builtin(key: Optional[str]) -> bool:
    return key in THEME_BY_KEY


def builtin_list() -> List[Dict[str, str]]:
    return [{"key": theme.key, "label": theme.label} for theme in BUILTIN_THEMES]


def default_theme() -> Dict[str, Optional[str]]:
    return {"key": DEFAULT_THEME, "label": THEME_BY_KEY[DEFAULT_THEME].label}


def clean_label(label: Optional[str]) -> str:
    """A typed theme label, trimmed and bounded; "" when there is nothing."""
    return " ".join((label or "").split())[:LABEL_MAX_CHARS]


def model_context(theme: Optional[Dict[str, Optional[str]]]) -> str:
    """The situations line a prompt gets: a built-in theme's description, or
    the learner's own words for an own / one-off theme."""
    if not theme:
        return THEME_BY_KEY[DEFAULT_THEME].description
    builtin = THEME_BY_KEY.get(theme.get("key") or "")
    if builtin is not None:
        return builtin.description
    return f"situations about: {theme.get('label') or ''} (the learner's own choice)"


def builtin_prompts(key: str) -> List[Dict[str, str]]:
    """A built-in theme's speaking prompts with ids ("<theme>:<n>").
    «Вперемешку» pools every other theme's prompts."""
    if key == MIXED:
        return [
            prompt
            for theme in BUILTIN_THEMES
            if theme.key != MIXED
            for prompt in builtin_prompts(theme.key)
        ]
    theme = THEME_BY_KEY[key]
    return [
        {"id": f"{key}:{number}", "question": question, "hint": hint}
        for number, (question, hint) in enumerate(theme.prompts)
    ]


def prompt_of_day(
    prompts: Sequence[Dict[str, str]], today: dt.date, offset: int = 0
) -> Dict[str, str]:
    """A different prompt each day, cycling the list; `offset` (in halves of
    the list) keeps «60 секунд» away from the day's monologue."""
    count = len(prompts)
    return dict(prompts[(today.toordinal() + offset * (count // 2)) % count])


def split_prompt_id(prompt_id: str) -> Tuple[str, int]:
    """"it_backend:3" -> ("it_backend", 3); ValueError when malformed."""
    key, _, number = (prompt_id or "").partition(":")
    if not key or not number.isdigit():
        raise ValueError(f"Bad prompt id {prompt_id!r}.")
    return key, int(number)
