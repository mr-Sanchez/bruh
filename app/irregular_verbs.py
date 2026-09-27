"""Irregular verbs: the table and its drill (pure - no I/O, no Claude).

The list is ordered by how often the verb is used, not alphabetically: the
first thirty cover most of what is actually spoken, so a learner who drills
from the top meets the useful ones first. The order is an approximation from
general English frequency lists (COCA / BNC style), authored by hand like
`curriculum.py` - never generated.

A form may have several accepted spellings ("burned / burnt", "got / gotten");
the first one is the one shown first. Grading is exact string matching after
normalisation - there is nothing here an LLM would do better, and the drill
must stay free.

Results are logged by `app.verb_store`; like dictation they stay out of the
learner model (an irregular form is not one of the taxonomy's speaking
mistakes) but count for the day streak.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Dict, Final, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Verb:
    rank: int
    base: Tuple[str, ...]
    past: Tuple[str, ...]
    participle: Tuple[str, ...]
    translation: str

    @property
    def key(self) -> str:
        """Stable id of the verb: its infinitive (unique across the table)."""
        return self.base[0]

    @property
    def forms(self) -> Tuple[Tuple[str, ...], ...]:
        return (self.base, self.past, self.participle)


# (infinitive, past simple, past participle, Russian translation), most frequent
# first. Variants are separated by "/". Translations are chosen so that no two
# verbs share one - the drill shows only the Russian.
_TABLE: Final[Tuple[Tuple[str, str, str, str], ...]] = (
    ("be", "was/were", "been", "быть"),
    ("have", "had", "had", "иметь"),
    ("do", "did", "done", "делать (выполнять)"),
    ("say", "said", "said", "сказать, говорить"),
    ("go", "went", "gone", "идти, ехать"),
    ("get", "got", "got/gotten", "получать; становиться"),
    ("make", "made", "made", "делать (создавать); заставлять"),
    ("know", "knew", "known", "знать"),
    ("think", "thought", "thought", "думать"),
    ("take", "took", "taken", "брать"),
    ("see", "saw", "seen", "видеть"),
    ("come", "came", "come", "приходить"),
    ("give", "gave", "given", "давать"),
    ("find", "found", "found", "находить"),
    ("tell", "told", "told", "рассказывать, сообщать"),
    ("become", "became", "become", "становиться (кем-то)"),
    ("feel", "felt", "felt", "чувствовать"),
    ("leave", "left", "left", "уходить, уезжать; оставлять"),
    ("put", "put", "put", "класть, ставить (куда-то)"),
    ("mean", "meant", "meant", "значить, иметь в виду"),
    ("keep", "kept", "kept", "хранить; продолжать (делать)"),
    ("let", "let", "let", "позволять, пускать"),
    ("begin", "began", "begun", "начинать"),
    ("bring", "brought", "brought", "приносить"),
    ("show", "showed", "shown/showed", "показывать"),
    ("hold", "held", "held", "держать; проводить (встречу)"),
    ("write", "wrote", "written", "писать"),
    ("stand", "stood", "stood", "стоять"),
    ("hear", "heard", "heard", "слышать"),
    ("run", "ran", "run", "бегать; управлять (компанией)"),
    ("meet", "met", "met", "встречать, знакомиться"),
    ("set", "set", "set", "устанавливать, задавать"),
    ("pay", "paid", "paid", "платить"),
    ("sit", "sat", "sat", "сидеть"),
    ("speak", "spoke", "spoken", "говорить (на языке), выступать"),
    ("lead", "led", "led", "вести, руководить"),
    ("read", "read", "read", "читать"),
    ("lose", "lost", "lost", "терять; проигрывать"),
    ("understand", "understood", "understood", "понимать"),
    ("grow", "grew", "grown", "расти; выращивать"),
    ("fall", "fell", "fallen", "падать"),
    ("send", "sent", "sent", "отправлять"),
    ("build", "built", "built", "строить"),
    ("spend", "spent", "spent", "тратить; проводить (время)"),
    ("cut", "cut", "cut", "резать"),
    ("win", "won", "won", "выигрывать"),
    ("buy", "bought", "bought", "покупать"),
    ("rise", "rose", "risen", "подниматься, расти (о ценах)"),
    ("choose", "chose", "chosen", "выбирать"),
    ("drive", "drove", "driven", "водить (машину)"),
    ("wear", "wore", "worn", "носить (одежду)"),
    ("deal", "dealt", "dealt", "иметь дело, справляться"),
    ("break", "broke", "broken", "ломать"),
    ("teach", "taught", "taught", "учить (кого-то), преподавать"),
    ("sell", "sold", "sold", "продавать"),
    ("catch", "caught", "caught", "ловить"),
    ("eat", "ate", "eaten", "есть (пищу)"),
    ("draw", "drew", "drawn", "рисовать; тянуть"),
    ("fight", "fought", "fought", "драться, бороться"),
    ("throw", "threw", "thrown", "бросать"),
    ("forget", "forgot", "forgotten", "забывать"),
    ("seek", "sought", "sought", "искать (книжн.), добиваться"),
    ("lie", "lay", "lain", "лежать"),
    ("learn", "learned/learnt", "learned/learnt", "учить (что-то), узнавать"),
    ("prove", "proved", "proved/proven", "доказывать"),
    ("hit", "hit", "hit", "ударять, попадать"),
    ("bear", "bore", "borne/born", "выносить, терпеть"),
    ("shoot", "shot", "shot", "стрелять; снимать (видео)"),
    ("hang", "hung", "hung", "вешать; висеть"),
    ("cost", "cost", "cost", "стоить"),
    ("sleep", "slept", "slept", "спать"),
    ("drink", "drank", "drunk", "пить"),
    ("fly", "flew", "flown", "летать"),
    ("shake", "shook", "shaken", "трясти; пожимать (руку)"),
    ("beat", "beat", "beaten", "бить; обыгрывать"),
    ("lay", "laid", "laid", "класть (горизонтально); накрывать (на стол)"),
    ("hurt", "hurt", "hurt", "причинять боль, ранить"),
    ("strike", "struck", "struck", "ударять; бастовать"),
    ("hide", "hid", "hidden", "прятать(ся)"),
    ("ride", "rode", "ridden", "ездить (верхом, на велосипеде)"),
    ("sing", "sang", "sung", "петь"),
    ("wake", "woke", "woken", "просыпаться; будить"),
    ("feed", "fed", "fed", "кормить"),
    ("steal", "stole", "stolen", "красть"),
    ("swim", "swam", "swum", "плавать"),
    ("blow", "blew", "blown", "дуть"),
    ("arise", "arose", "arisen", "возникать"),
    ("forgive", "forgave", "forgiven", "прощать"),
    ("light", "lit/lighted", "lit/lighted", "зажигать; освещать"),
    ("lend", "lent", "lent", "одалживать (кому-то)"),
    ("spread", "spread", "spread", "распространять(ся); намазывать"),
    ("freeze", "froze", "frozen", "замерзать; замораживать"),
    ("shut", "shut", "shut", "закрывать, захлопывать"),
    ("stick", "stuck", "stuck", "приклеивать; застревать"),
    ("dream", "dreamed/dreamt", "dreamed/dreamt", "мечтать; видеть сон"),
    ("burn", "burned/burnt", "burned/burnt", "гореть; сжигать"),
    ("undertake", "undertook", "undertaken", "предпринимать, браться за"),
    ("overcome", "overcame", "overcome", "преодолевать"),
    ("withdraw", "withdrew", "withdrawn", "снимать (деньги); отзывать"),
    ("upset", "upset", "upset", "расстраивать"),
    ("ring", "rang", "rung", "звонить; звенеть"),
    ("sink", "sank", "sunk", "тонуть"),
    ("bend", "bent", "bent", "сгибать(ся), наклоняться"),
    ("tear", "tore", "torn", "рвать"),
    ("bite", "bit", "bitten", "кусать"),
    ("dig", "dug", "dug", "копать"),
    ("shine", "shone/shined", "shone/shined", "светить, сиять"),
    ("swing", "swung", "swung", "качать(ся), размахивать"),
    ("split", "split", "split", "раскалывать, делить"),
    ("quit", "quit/quitted", "quit/quitted", "бросать (привычку), увольняться"),
    ("bet", "bet", "bet", "делать ставку, держать пари"),
    ("bind", "bound", "bound", "связывать, обязывать"),
    ("lean", "leaned/leant", "leaned/leant", "наклоняться, опираться"),
    ("mislead", "misled", "misled", "вводить в заблуждение"),
    ("misunderstand", "misunderstood", "misunderstood", "неправильно понимать"),
    ("forbid", "forbade/forbad", "forbidden", "запрещать"),
    ("broadcast", "broadcast/broadcasted", "broadcast/broadcasted", "транслировать, вещать"),
    ("forecast", "forecast/forecasted", "forecast/forecasted", "прогнозировать"),
    ("cast", "cast", "cast", "отбрасывать (тень); отдавать (голос)"),
    ("fit", "fit/fitted", "fit/fitted", "подходить (по размеру)"),
    ("sweep", "swept", "swept", "подметать"),
    ("swear", "swore", "sworn", "клясться; ругаться"),
    ("weep", "wept", "wept", "плакать, рыдать"),
    ("spill", "spilled/spilt", "spilled/spilt", "проливать"),
    ("spoil", "spoiled/spoilt", "spoiled/spoilt", "портить; баловать"),
    ("smell", "smelled/smelt", "smelled/smelt", "пахнуть; нюхать"),
    ("spell", "spelled/spelt", "spelled/spelt", "произносить по буквам"),
    ("leap", "leaped/leapt", "leaped/leapt", "прыгать, скакать"),
    ("kneel", "knelt/kneeled", "knelt/kneeled", "становиться на колени"),
    ("bleed", "bled", "bled", "кровоточить"),
    ("breed", "bred", "bred", "разводить (животных)"),
    ("creep", "crept", "crept", "ползти, красться"),
    ("cling", "clung", "clung", "цепляться"),
    ("grind", "ground", "ground", "молоть, перемалывать"),
    ("flee", "fled", "fled", "убегать, спасаться бегством"),
    ("slide", "slid", "slid", "скользить"),
    ("spin", "spun", "spun", "вращать(ся), крутить"),
    ("sting", "stung", "stung", "жалить"),
    ("strive", "strove/strived", "striven/strived", "стремиться, прилагать усилия"),
    ("shrink", "shrank/shrunk", "shrunk", "сжиматься, уменьшаться"),
    ("weave", "wove", "woven", "ткать, плести"),
    ("wind", "wound", "wound", "заводить (часы); виться"),
    ("dwell", "dwelt/dwelled", "dwelt/dwelled", "проживать; зацикливаться (на мысли)"),
    ("burst", "burst", "burst", "лопаться, взрываться"),
    ("shed", "shed", "shed", "сбрасывать (листья, вес)"),
    ("spring", "sprang/sprung", "sprung", "вскакивать, возникать внезапно"),
    ("speed", "sped/speeded", "sped/speeded", "мчаться; превышать скорость"),
    ("sew", "sewed", "sewn/sewed", "шить"),
    ("undo", "undid", "undone", "отменять; расстёгивать"),
)


def _split(cell: str) -> Tuple[str, ...]:
    return tuple(part.strip() for part in cell.split("/") if part.strip())


VERBS: Final[Tuple[Verb, ...]] = tuple(
    Verb(rank, _split(base), _split(past), _split(participle), translation)
    for rank, (base, past, participle, translation) in enumerate(_TABLE, start=1)
)
VERBS_BY_KEY: Final[Dict[str, Verb]] = {verb.key: verb for verb in VERBS}
FORM_NAMES: Final[Tuple[str, str, str]] = ("base", "past", "participle")

# A verb counts as learned after this many correct answers in a row.
LEARNED_STREAK: Final[int] = 3

# Anything between variants the learner may type: "was/were", "was, were".
_SEPARATORS = re.compile(r"[\s/,;|]+")


def verb_payload(verb: Verb) -> Dict[str, Any]:
    return {
        "key": verb.key,
        "rank": verb.rank,
        "base": list(verb.base),
        "past": list(verb.past),
        "participle": list(verb.participle),
        "translation": verb.translation,
    }


# ------------------------------------------------------------------ grading
def _tokens(answer: str) -> List[str]:
    text = answer.strip().lower().replace("’", "'")
    return [token for token in _SEPARATORS.split(text) if token]


def grade_form(answer: str, accepted: Sequence[str]) -> bool:
    """Right when something was typed and every typed variant is an accepted one.

    Typing one variant is enough ("was" for "was/were"); a wrong extra
    variant makes the answer wrong, so guessing both ways does not pass.
    """
    tokens = _tokens(answer)
    allowed = {form.lower() for form in accepted}
    return bool(tokens) and all(token in allowed for token in tokens)


def grade(verb: Verb, answers: Sequence[str]) -> Dict[str, Any]:
    """Grade the three typed forms of a verb (infinitive, past, participle)."""
    padded = list(answers)[:3] + [""] * (3 - len(answers))
    forms = [
        {
            "form": name,
            "answer": answer,
            "correct": grade_form(answer, accepted),
            "expected": list(accepted),
        }
        for name, answer, accepted in zip(FORM_NAMES, padded, verb.forms)
    ]
    return {
        "verb": verb.key,
        "forms": forms,
        "correct": all(form["correct"] for form in forms),
    }


# -------------------------------------------------------------- statistics
def verb_stats(results: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per verb: attempts, correct answers, the current run of correct ones, last result.

    `results` must be in log order (oldest first), as the store reads them.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for record in results:
        key = record.get("verb")
        if key not in VERBS_BY_KEY:
            continue
        row = stats.setdefault(
            key, {"attempts": 0, "correct": 0, "streak": 0, "last_correct": None, "last_at": None}
        )
        ok = bool(record.get("correct"))
        row["attempts"] += 1
        row["correct"] += int(ok)
        row["streak"] = row["streak"] + 1 if ok else 0
        row["last_correct"] = ok
        row["last_at"] = record.get("ts")
    for row in stats.values():
        row["learned"] = row["streak"] >= LEARNED_STREAK
    return stats


def summary(stats: Mapping[str, Mapping[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(VERBS),
        "trained": len(stats),
        "learned": sum(1 for row in stats.values() if row["learned"]),
        "to_fix": sum(1 for row in stats.values() if row["last_correct"] is False),
    }


def pick(stats: Mapping[str, Mapping[str, Any]], count: int) -> List[Verb]:
    """The verbs for a drill of `count`, most useful first.

    1. verbs answered wrong last time (most frequent first) - fix mistakes;
    2. verbs never drilled, in frequency order - so new ones come from the top;
    3. verbs not yet learned, the longest-unseen first;
    4. learned verbs, the longest-unseen first - a light review.
    """

    def order(verb: Verb) -> Tuple[int, str, int]:
        row = stats.get(verb.key)
        if row is None:
            return (1, "", verb.rank)
        if row["last_correct"] is False:
            return (0, "", verb.rank)
        group = 3 if row["learned"] else 2
        return (group, str(row["last_at"] or ""), verb.rank)

    return sorted(VERBS, key=order)[: max(0, count)]


def active_days(results: Iterable[Mapping[str, Any]]) -> set:
    days = set()
    for record in results:
        try:
            days.add(dt.datetime.fromisoformat(str(record["ts"])).date())
        except (KeyError, ValueError):
            continue
    return days


def lookup(key: str) -> Optional[Verb]:
    return VERBS_BY_KEY.get(key)
