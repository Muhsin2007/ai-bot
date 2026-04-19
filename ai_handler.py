"""
AI handler — Claude API, language detection, intent detection.
"""
import asyncio
import json
import logging
import os
import random

import anthropic
from dotenv import load_dotenv

from storage_db import (
    load_prices, load_summary, load_client_facts, save_client_facts,
    search_training_qa, save_training_qa,
)

load_dotenv()
_ai  = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
log  = logging.getLogger("tat_auto")


# ══════════════════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_UZ_CYRILLIC_UNIQUE = set("ЎўҚқҒғҲҳ")

# Надёжные маркеры узбекского в кириллице
_UZ_CYRILLIC_WORDS = [
    "нарх", "нархи", "қанча", "канча", "нархлар",
    "рахмат", "раҳмат", "яхши", "яхшими", "хайр",
    "ака", "опа", "биродар",
    "манзил", "қаерда", "кайерда",
    "насия", "фоиз", "тўлов",
    "билан", "учун", "эмас", "ҳам", "лекин", "аммо", "агар",
    "сизда", "бизда", "сизни", "менинг", "бизнинг",
    "йил", "йилга", "ойга",
    "берасиз", "оласиз", "қиласиз",
    "бўлади", "булади", "ишлайди",
    "айтинг", "биласизми", "юборасиз",
    "савдо", "нима", "сизга", "менга",
    "кўрсат", "автони", "машинани",
    "йўқ", "синов", "юруши",
    "ассалому", "алайкум",
    "олиш", "сотиб",
]

# Надёжные маркеры узбекского латиницей
_UZ_LATIN_WORDS = [
    "salom", "rahmat", "yaxshi", "narx", "narxi", "qancha",
    "manzil", "nasiya", "savdo", "nima", "hammasi",
    "sizga", "menga", "ko'rsat", "ko'rsating", "berasizmi",
    "bo'ladi", "yo'q", "olish", "sotib",
    "ishlaydi", "foiz", "assalomu", "alaykum",
    "xayr", "kerak", "qayerda", "qayer",
    "sinov", "yuruvi", "bilan", "uchun", "emas", "ham",
    "lekin", "agar", "sizda", "bizda", "yil", "oy",
    "tolov", "stavka", "aka", "opa", "xarid",
    "rasm", "rasmlar", "mashina", "avto",
]


def detect_language(text: str) -> str:
    """Возвращает 'uz', 'ru' или 'en'."""
    # 1. Уникальные узбекские буквы (Ў, Қ, Ғ, Ҳ)
    if any(c in _UZ_CYRILLIC_UNIQUE for c in text):
        return "uz"

    t = text.lower()

    # 2. Узбекские кириллические слова
    if any(w in t for w in _UZ_CYRILLIC_WORDS):
        return "uz"

    # 3. Узбекские латинские слова
    if any(w in t for w in _UZ_LATIN_WORDS):
        return "uz"

    # 4. Латиница vs кириллица
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    latin    = sum(1 for c in text if c.isalpha() and c.isascii())
    return "en" if latin > cyrillic else "ru"


# ══════════════════════════════════════════════════════════════════════════════
# OPT-OUT DETECTION  — клиент просит больше не писать
# ══════════════════════════════════════════════════════════════════════════════

_OPTOUT_KW = [
    # Русский
    "уже купил", "уже купила", "уже взял", "уже взяла",
    "не интересует", "не интересуюсь", "неинтересует",
    "не пишите", "не пишите мне", "не беспокойте",
    "хватит писать", "больше не пишите", "прекратите писать",
    "не надо писать", "нашёл другой", "нашла другой", "купил у других",
    "взял у других", "взяла у других", "отстаньте",
    "не беспокойте меня", "купил в другом",
    # Uzbek Latin
    "sotib oldim", "xarid qildim", "qiziqmayapman",
    "yozmang", "yozmang menga", "kerak emas",
    "boshqasidan oldim", "qayta yozmang", "bezovta qilmang",
    # Uzbek Cyrillic
    "сотиб олдим", "харид қилдим", "қизиқмаяпман",
    "ёзманг", "ёзманг менга", "керак эмас",
    "бошқасидан олдим", "қайта ёзманг",
    # English
    "already bought", "not interested", "stop messaging",
    "don't message", "don't contact", "remove me", "unsubscribe",
]

# Вежливые прощания при отписке (на 3 языках)
OPTOUT_FAREWELL = {
    "ru": "Понял, больше не буду беспокоить. Если когда-нибудь понадобится помощь — мы всегда здесь.",
    "uz": "Tushundim, endi bezovta qilmayman. Kerak bo'lsa — har doim yordam berishga tayyorman.",
    "en": "Understood, I won't bother you anymore. Feel free to reach out anytime.",
}


def detect_optout(text: str) -> bool:
    """True если клиент просит прекратить общение."""
    t = text.lower()
    return any(kw in t for kw in _OPTOUT_KW)


# ══════════════════════════════════════════════════════════════════════════════
# BUYING INTENT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_BUYING_INTENT_KW = [
    # Русский
    "хочу купить", "готов купить", "куплю", "оформляйте", "оформите",
    "беру", "возьму", "давайте оформим", "готов к покупке",
    "когда могу забрать", "когда можно забрать", "готов платить",
    "как оформить", "как купить", "хочу оформить", "берём",
    # Uzbek Latin
    "sotib olmoqchi", "sotib olaman", "rasmiylashtiring",
    "olaman", "xarid qilmoqchi", "qachon olsam bo'ladi",
    "to'lashga tayyorman", "qanday rasmiylashtirish",
    # Uzbek Cyrillic
    "сотиб олмоқчи", "сотиб оламан", "расмийлаштиринг",
    "оламан", "харид қилмоқчи", "тўлашга тайёрман",
    # English
    "ready to buy", "want to buy", "i'll take it",
    "let's proceed", "how to purchase", "ready to purchase",
]


def detect_buying_intent(text: str) -> bool:
    """True если клиент явно намерен купить."""
    t = text.lower()
    return any(kw in t for kw in _BUYING_INTENT_KW)


# ══════════════════════════════════════════════════════════════════════════════
# GREETING DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_GREETING_WORDS = {
    "ассалому алайкум", "ассалому алейкум", "assalomu alaykum",
    "salom", "салом", "привет", "здравствуйте", "здравствуй",
    "добрый день", "добрый вечер", "доброе утро",
    "hello", "hi", "hey", "хай", "салам", "вечер добрый",
}


def is_greeting_only(text: str) -> bool:
    """True если сообщение — только приветствие без вопроса."""
    t = text.lower().strip().rstrip("!.,?")
    # Прямое совпадение
    if t in _GREETING_WORDS:
        return True
    # Короткое сообщение содержит только приветствие
    words = t.split()
    if len(words) <= 4 and any(w in _GREETING_WORDS for w in [t, " ".join(words[:2])]):
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# COMPETITOR DETECTOR  (реальные данные)
# ══════════════════════════════════════════════════════════════════════════════

_COMPETITORS: dict[str, dict] = {
    "byd": {
        "name": "BYD",
        "kw": ["byd", "бид", "бйд", "bid"],
        "facts": {
            "ru": (
                "BYD — массовый бренд. Voyah — премиум-линейка Dongfeng, "
                "платформа ESSA разработана совместно с Nissan. "
                "Voyah Courage: 650 км реального запаса, батарея CATL с гарантией 8 лет. "
                "У Voyah Free воздушная подвеска и двойная шумоизоляция — у BYD этого нет."
            ),
            "uz": (
                "BYD — ommaviy brend. Voyah — Dongfeng'ning premium liniyasi, "
                "ESSA platformasi Nissan bilan yaratilgan. "
                "Voyah Courage: 650 km real yurish, CATL batareyasiga 8 yil kafolat. "
                "Voyah Free'da havo suspenziyasi va ikki qavatli izolyatsiya bor."
            ),
            "en": (
                "BYD is mass-market. Voyah is Dongfeng's premium line, "
                "ESSA platform co-developed with Nissan. "
                "Voyah Courage: 650 km real range, CATL battery 8-year warranty. "
                "Voyah Free has air suspension and double insulation — BYD lacks these."
            ),
        },
    },
    "haval": {
        "name": "Haval",
        "kw": ["haval", "хавал", "хавейл"],
        "facts": {
            "ru": (
                "Haval — гибрид (PHEV), всё равно нужен бензин. "
                "Voyah — чистый электромобиль: нет топлива, ТО раз в 2 года. "
                "Стоимость езды: ~5 000 сум/100 км против ~50 000 сум у Haval."
            ),
            "uz": (
                "Haval — gibrid, baribir benzin kerak. "
                "Voyah — to'liq elektromobil, yoqilg'i yo'q, TO 2 yilda bir marta. "
                "Haydash narxi: Voyah ~5 000 so'm/100 km, Haval ~50 000 so'm/100 km."
            ),
            "en": (
                "Haval is a PHEV — still needs gasoline. "
                "Voyah is pure EV: no fuel, service every 2 years. "
                "Running cost: ~5,000 UZS/100km vs ~50,000 UZS with Haval."
            ),
        },
    },
    "chery": {
        "name": "Chery / Omoda",
        "kw": ["chery", "чери", "omoda", "омода", "exeed", "эксид"],
        "facts": {
            "ru": (
                "Chery/Omoda — бензиновый сегмент. Voyah — электрический премиум. "
                "Voyah Free: HUD, ADAS Level 2+, массаж кресел — всё в базе. "
                "За 3 года экономия на топливе перекрывает разницу в цене."
            ),
            "uz": (
                "Chery/Omoda — benzin segment. Voyah — elektr premium. "
                "Voyah Free: HUD, ADAS Level 2+, o'rindiq massaji — hammasi bazada. "
                "3 yilda yoqilg'i tejamkorligi narx farqini qoplaydi."
            ),
            "en": (
                "Chery/Omoda are gasoline. Voyah is electric premium. "
                "Voyah Free: HUD, ADAS Level 2+, seat massage — all standard. "
                "Fuel savings over 3 years offset the price difference."
            ),
        },
    },
    "geely": {
        "name": "Geely",
        "kw": ["geely", "джили", "гили"],
        "facts": {
            "ru": (
                "Geely хорош, но Voyah построен на платформе ESSA совместно с Nissan. "
                "Voyah Free: полный привод, до 655 км, батарея CATL. "
                "Гарантийный сервис — напрямую в TAT AUTO Ташкент."
            ),
            "uz": (
                "Geely yaxshi, lekin Voyah Nissan bilan ESSA platformasida qurilgan. "
                "Voyah Free: to'liq yetakchi, 655 km gacha, CATL batareyasi. "
                "Kafolat servisi — TAT AUTO Toshkentda to'g'ridan-to'g'ri."
            ),
            "en": (
                "Geely is good, but Voyah is built on ESSA platform with Nissan. "
                "Voyah Free: AWD, up to 655 km, CATL battery. "
                "Full warranty service directly at TAT AUTO Tashkent."
            ),
        },
    },
    "toyota": {
        "name": "Toyota",
        "kw": ["toyota", "тойота"],
        "facts": {
            "ru": (
                "Toyota — надёжная, но бензиновая. "
                "Voyah: нет масла, свечей, ремня ГРМ — ТО в 5 раз дешевле. "
                "За 5 лет экономия 30–50 млн сум. Плюс: полный экран, air suspension, ADAS."
            ),
            "uz": (
                "Toyota — ishonchli, lekin benzinli. "
                "Voyah: moy, shamlar, kamar yo'q — TO 5 baravar arzon. "
                "5 yilda 30–50 mln so'm tejash. Bonus: to'liq ekran, havo suspenziyasi, ADAS."
            ),
            "en": (
                "Toyota is reliable but gasoline. "
                "Voyah: no oil, plugs, or timing belt — service 5x cheaper. "
                "5-year savings: 30–50M UZS. Plus: full screen, air suspension, ADAS."
            ),
        },
    },
    "kia": {
        "name": "Kia",
        "kw": ["kia", "киа"],
        "facts": {
            "ru": (
                "Kia EV6/EV9 — достойный выбор, но сервис в Узбекистане ограничен. "
                "TAT AUTO обеспечивает полное гарантийное обслуживание в Ташкенте. "
                "Voyah Courage: 650 км, CATL с гарантией 8 лет."
            ),
            "uz": (
                "Kia EV6/EV9 — yaxshi, lekin O'zbekistonda servis cheklangan. "
                "TAT AUTO Toshkentda to'liq kafolat xizmatini ta'minlaydi. "
                "Voyah Courage: 650 km, CATL batareyasiga 8 yil kafolat."
            ),
            "en": (
                "Kia EV6/EV9 is good but Uzbekistan service is limited. "
                "TAT AUTO provides full warranty in Tashkent. "
                "Voyah Courage: 650 km, CATL 8-year warranty."
            ),
        },
    },
    "hyundai": {
        "name": "Hyundai",
        "kw": ["hyundai", "хёндай", "хундай", "хундэ"],
        "facts": {
            "ru": (
                "Hyundai IONIQ — сильный конкурент. "
                "Voyah выигрывает: воздушная подвеска, массаж, HUD — всё в базе. "
                "У IONIQ 5 это опции за доплату. "
                "Запас: Voyah Courage 650 км против 481 км у IONIQ 5."
            ),
            "uz": (
                "Hyundai IONIQ — kuchli raqib. "
                "Voyah yutadi: havo suspenziyasi, massaj, HUD — hammasi bazada. "
                "IONIQ 5'da bular qo'shimcha to'lov. "
                "Voyah Courage 650 km, IONIQ 5 — 481 km."
            ),
            "en": (
                "Hyundai IONIQ is strong. "
                "Voyah wins: air suspension, seat massage, HUD — all standard. "
                "IONIQ 5 charges extra. Range: 650 km vs 481 km."
            ),
        },
    },
    "changan": {
        "name": "Changan / Deepal",
        "kw": ["changan", "чанган", "deepal", "дипал"],
        "facts": {
            "ru": (
                "Changan/Deepal — доступный сегмент, Voyah — другой класс: "
                "кожа Nappa, двойная шумоизоляция, воздушная подвеска. "
                "Для дальних поездок Ташкент–Самарканд–Бухара Voyah вне конкуренции."
            ),
            "uz": (
                "Changan/Deepal — arzon segment. Voyah — boshqa sinf: "
                "Nappa charm, ikki qavatli izolyatsiya, havo suspenziyasi. "
                "Toshkent–Samarqand–Buxoro safarlari uchun Voyah raqobatsiz."
            ),
            "en": (
                "Changan/Deepal is budget, Voyah is different class: "
                "Nappa leather, double insulation, air suspension. "
                "For Tashkent–Samarkand–Bukhara trips, Voyah is unmatched."
            ),
        },
    },
}


def detect_competitor(text: str) -> str | None:
    t = text.lower()
    for key, data in _COMPETITORS.items():
        if any(kw in t for kw in data["kw"]):
            return key
    return None


def get_competitor_facts(competitor_key: str, lang: str = "ru") -> str:
    comp = _COMPETITORS.get(competitor_key)
    if not comp:
        return ""
    return comp["facts"].get(lang) or comp["facts"].get("ru", "")


# ══════════════════════════════════════════════════════════════════════════════
# TRADE-IN DETECTOR
# ══════════════════════════════════════════════════════════════════════════════

_TRADEIN_KW = [
    "cobalt", "кобалт", "nexia", "нексия", "matiz", "матиз",
    "lacetti", "лачетти", "spark", "спарк", "damas", "дамас",
    "tracker", "трекер", "captiva", "каптива", "gentra", "джентра",
    "malibu", "малибу", "equinox", "trailblazer",
    "accent", "sonata", "tucson", "sorento", "sportage",
    "camry", "corolla", "prado", "land cruiser",
    "старая машина", "свою машину", "обменять",
    "трейд-ин", "trade-in", "trade in",
    "продам машину", "сдать машину", "зачёт авто", "мою машину",
    "старый автомобиль", "свой авто",
    "eski mashina", "eski avto", "sotmoq", "almashmoq",
    "эски машина", "эски авто", "алмаштирмоқ",
]


def detect_tradein(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _TRADEIN_KW)


# ══════════════════════════════════════════════════════════════════════════════
# TEST DRIVE DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_TESTDRIVE_KW = [
    "тест-драйв", "тест драйв", "тестдрайв",
    "хочу проехать", "хочу попробовать", "можно попробовать",
    "записаться на тест", "прокатиться",
    "test drive", "test-drive",
    "sinov yuruvi", "urib ko'rmoq", "haydab ko'rmoq",
    "синов юруши", "ҳайдаб кўрмоқ", "уриб кўрмоқ",
]

_TESTDRIVE_DECLINE_KW = [
    "не хочу тест", "не нужен тест", "без тест", "не надо тест",
    "test drive kerak emas", "sinov kerak emas",
    "тест драйв керак эмас",
]


def detect_testdrive(text: str) -> bool:
    t = text.lower()
    if any(kw in t for kw in _TESTDRIVE_DECLINE_KW):
        return False
    return any(kw in t for kw in _TESTDRIVE_KW)


# ══════════════════════════════════════════════════════════════════════════════
# FOLLOW-UP MESSAGES  (хранятся для справки, автоотправка отключена)
# ══════════════════════════════════════════════════════════════════════════════

_FOLLOWUP: dict[str, dict[int, list[str]]] = {
    "ru": {
        1: [
            "Здравствуйте{name}! Как продвигается выбор? Если есть вопросы — с удовольствием помогу.",
            "Добрый день{name}! Хотел уточнить — остались ли вопросы по автомобилям.",
        ],
        2: [
            "Здравствуйте{name}! Напоминаю, что у нас можно записаться на тест-драйв в любое удобное время.",
            "Добрый день{name}! Готовы принять Вас в салоне. Может, запишемся на тест-драйв.",
        ],
        3: [
            "Здравствуйте{name}! Последний раз пишу — не хочу беспокоить. Если надумаете — мы здесь.",
        ],
    },
    "uz": {
        1: [
            "Assalomu alaykum{name}! Avtomobil tanlash qanday ketayapti. Savollaringiz bo'lsa, yordam berishga tayyorman.",
        ],
        2: [
            "Assalomu alaykum{name}! Test-drive uchun qulay vaqtda yozilishingiz mumkin.",
        ],
        3: [
            "Assalomu alaykum{name}! Oxirgi marta yozyapman. Qaror qilsangiz — biz doimo bu yerdamiz.",
        ],
    },
    "en": {
        1: [
            "Hello{name}! How is your car search going. Happy to help if you have questions.",
        ],
        2: [
            "Hello{name}! Just a reminder — you're welcome to book a test drive anytime.",
        ],
        3: [
            "Hello{name}! This is my last message — I don't want to bother you. We're here if you need us.",
        ],
    },
}


def get_followup_message(stage: int, lang: str = "ru", name: str = "") -> str:
    lang  = lang if lang in _FOLLOWUP else "ru"
    stage = min(stage, 3)
    name_str = f", {name}" if name else ""
    msgs = _FOLLOWUP[lang].get(stage, _FOLLOWUP["ru"][stage])
    return random.choice(msgs).format(name=name_str)


# ══════════════════════════════════════════════════════════════════════════════
# HESITATION DETECTION — клиент колеблется / откладывает
# ══════════════════════════════════════════════════════════════════════════════

# Используем стебли слов — работают лучше для русской морфологии
_HESITATION_HIGH = [
    # Русский — прямое откладывание
    "подумаю", "подумать", "подума",
    "посмотрю", "посмотреть",
    "не спешу", "не тороплюсь",
    "посоветуюсь", "посоветоваться", "посовет",
    "обсужу", "обсудим", "обсуд",
    "не готов", "не готова",
    "позже", "попозже", "потом напишу", "позднее напиш",
    # Узбекский
    "o'ylab ko'raman", "o'ylab ko'ray", "keyin yozaman",
    "maslahatlash", "maslahat qilib",
    "qaror qilmad", "shoshilmayapman", "shoshilmayman",
    "ойлаб кўраман", "кейин ёзаман", "маслаҳатлаш",
]

_HESITATION_MEDIUM = [
    # Русский — сравнение / неопределённость
    "сравниваю", "смотрю варианты", "рассматриваю",
    "ещё не решил", "ещё не решила",
    "не решил", "не решила",
    "пока не знаю", "раздумываю", "взвешиваю",
    # Узбекский
    "solishtiryapman", "solishtirmoqchiman",
    "hali bilmayman", "qaror qilmadim",
    "солиштиряпман", "ҳали билмайман",
]

_HESITATION_LOW = [
    # Русский — ценовое возражение / слабое сомнение
    "дорого", "дороговато", "дороговат",
    "немного дорого", "чуть дорого",
    "не уверен", "не уверена",
    "подумаем", "посмотрим", "может быть", "возможно",
    "наверное", "наверно",
    # Узбекский
    "qimmat", "qimmatroq", "biroz qimmat",
    "bilmayman", "balki", "ehtimol",
    "қиммат", "билмайман", "балки",
]


def detect_hesitation(text: str) -> tuple[str | None, float]:
    """
    Возвращает (уровень, score) или (None, 0.0).
    Уровни: 'высокий' (0.9) / 'средний' (0.6) / 'слабый' (0.3).

    Снижает score вдвое если сообщение длинное и содержит '?' —
    скорее всего это вопрос, а не отказ.
    """
    t = text.lower()
    level, score = None, 0.0

    if any(kw in t for kw in _HESITATION_HIGH):
        level, score = "высокий", 0.9
    elif any(kw in t for kw in _HESITATION_MEDIUM):
        level, score = "средний", 0.6
    elif any(kw in t for kw in _HESITATION_LOW):
        level, score = "слабый", 0.3

    # Длинное сообщение с вопросом → снижаем уверенность
    if level and len(text) > 60 and "?" in text:
        score *= 0.5
        if score < 0.25:
            level, score = None, 0.0

    return level, score


# ══════════════════════════════════════════════════════════════════════════════
# STICKER INTERPRETATION  — стикер → сигнал намерения клиента
# ══════════════════════════════════════════════════════════════════════════════

# Каждый набор — эмодзи одного эмоционального сигнала.
# Порядок важен: проверяем от наиболее специфичных к общим.
_STICKER_SIGNALS: list[tuple[frozenset, str]] = [
    (frozenset({"🚀", "💥", "⚡", "🤑", "🤩", "😍", "💰", "🏆", "🎯", "🔥", "💎"}),
     "excitement"),
    (frozenset({"👍", "👏", "🤝", "✅", "💪", "🙌", "❤️", "🫡", "😊", "😀", "😁",
                "🥰", "😃", "😄", "⭐", "🌟", "💯", "🫶", "❤️‍🔥", "🎉", "🥳"}),
     "approval"),
    (frozenset({"🙏", "😌", "🫂", "🥹", "😇", "🤲"}),
     "gratitude"),
    (frozenset({"🤔", "🧐", "😐", "😑", "🫤", "😶", "🤨", "🤷"}),
     "hesitation"),
    (frozenset({"😕", "😵", "🫠", "🙃", "😵‍💫", "❓", "🤦"}),
     "confusion"),
    (frozenset({"😂", "🤣", "😆", "😛", "🤪", "😜", "😝", "🤭", "😸"}),
     "humor"),
    (frozenset({"😤", "😠", "😡", "🤬", "😞", "😔", "😣", "😖", "😫", "😒"}),
     "frustration"),
    (frozenset({"👎", "🚫", "❌", "🙅", "🛑", "⛔", "🙈"}),
     "rejection"),
    (frozenset({"👋", "✌️", "🤚", "🖐️", "🫱", "😴"}),
     "farewell"),
]

_STICKER_SYNTHETIC: dict[str, str] = {
    "excitement":  "[стикер: восторг / сильный интерес]",
    "approval":    "[стикер: одобрение / позитив]",
    "gratitude":   "[стикер: благодарность]",
    "hesitation":  "[стикер: раздумье / сомнение]",
    "confusion":   "[стикер: непонимание / вопрос]",
    "humor":       "[стикер: смех / лёгкое настроение]",
    "frustration": "[стикер: недовольство / раздражение]",
    "rejection":   "[стикер: отказ / негатив]",
    "farewell":    "[стикер: прощание]",
    "unknown":     "[стикер]",
}

_STICKER_HINTS: dict[str, str] = {
    "excitement": (
        "[Клиент прислал стикер — явный восторг и высокий интерес. Горячий лид. "
        "Воспользуйся моментом: предложи записаться на тест-драйв или "
        "уточни когда удобно приехать в салон. Один конкретный шаг.]"
    ),
    "approval": (
        "[Клиент прислал стикер одобрения — настроен позитивно. "
        "Продолжи разговор: задай один уточняющий вопрос или предложи "
        "следующий шаг — тест-драйв, запись, обсуждение кредита.]"
    ),
    "gratitude": (
        "[Клиент прислал стикер благодарности. Ответь тепло и кратко, "
        "затем мягко предложи следующий шаг — тест-драйв или встречу в салоне. "
        "Не давай лишней информации.]"
    ),
    "hesitation": (
        "[Клиент прислал стикер с сомнением. Восприми как колебание. "
        "Не дави. Мягко спроси что именно вызывает вопрос. "
        "Помоги с возражением — один короткий вопрос.]"
    ),
    "confusion": (
        "[Клиент прислал стикер — похоже что-то непонятно. "
        "Спроси что именно вызвало вопрос. "
        "Будь максимально конкретным и полезным. Одно предложение.]"
    ),
    "humor": (
        "[Клиент прислал смешной стикер — лёгкая дружеская атмосфера. "
        "Ответь в тёплом непринуждённом тоне, "
        "затем плавно верни к теме автомобиля.]"
    ),
    "frustration": (
        "[Клиент прислал стикер раздражения или недовольства. "
        "Деэскалация: прими с пониманием, спроси что пошло не так. "
        "Не оправдывайся. Будь на стороне клиента. Один вопрос.]"
    ),
    "rejection": (
        "[Клиент прислал стикер отказа. Прими спокойно, без давления. "
        "Спроси что именно не подходит — возможно есть другой вариант. "
        "Один короткий вопрос, без уговоров.]"
    ),
    "farewell": (
        "[Клиент прислал прощальный стикер. "
        "Попрощайся тепло, скажи что всегда рад помочь. Одно предложение.]"
    ),
    "unknown": (
        "[Клиент прислал стикер. Реагируй естественно на основе "
        "контекста предыдущего разговора. Короткий дружелюбный ответ.]"
    ),
}


def interpret_sticker(emoji: str) -> tuple[str, str, str]:
    """
    По эмодзи стикера возвращает (signal, synthetic_text, ai_hint).

    signal        — категория: approval / hesitation / humor / ...
    synthetic_text — текст сохраняемый в историю как «сообщение» клиента
    ai_hint        — инструкция для AI как реагировать на этот стикер

    Алгоритм: ищем каждый символ из emoji-строки в наборах.
    Строка может содержать несколько эмодзи — берём первое совпадение.
    """
    for emoji_set, signal in _STICKER_SIGNALS:
        if any(ch in emoji_set for ch in (emoji or "")):
            return signal, _STICKER_SYNTHETIC[signal], _STICKER_HINTS[signal]

    hint = (
        f"[Клиент прислал стикер (эмодзи: {emoji!r}). "
        f"Реагируй естественно на основе контекста разговора.]"
        if emoji else _STICKER_HINTS["unknown"]
    )
    return "unknown", _STICKER_SYNTHETIC["unknown"], hint


# ══════════════════════════════════════════════════════════════════════════════
# NASIYA / INSTALLMENT CALCULATION DETECTION  — роутим к @Deepaluz
# ══════════════════════════════════════════════════════════════════════════════

_NASIYA_CALC_KW = [
    # Русский — расчёт платежей
    "сколько в месяц", "ежемесячный платёж", "платёж в месяц",
    "посчитай кредит", "рассчитай кредит", "расчёт кредита",
    "посчитай рассрочку", "рассчитай рассрочку",
    "рассчитай насия", "насия рассчитай", "насия расчёт",
    "сколько выйдет в месяц", "ежемесячный взнос",
    "кредитный калькулятор", "рассчитай платёж",
    "какой будет платёж", "сколько платить в месяц",
    # Узбекский latin
    "oylik tolov", "oylik to'lov", "oyiga qancha",
    "oylik qancha", "hisoblang", "kredit hisob",
    "nasiya hisob", "nasiya hisobi", "oylik hisob",
    # Узбекский cyrillic
    "ойлик тўлов", "ойига қанча", "ойлик қанча",
    "ҳисобланг", "кредит ҳисоб", "насия ҳисоб",
]


def detect_nasiya_calc(text: str) -> bool:
    """
    True если клиент просит рассчитать ежемесячный платёж по кредиту или насия.
    Такие запросы роутятся к @Deepaluz — бот никогда не изобретает цифры.
    """
    t = text.lower()
    return any(kw in t for kw in _NASIYA_CALC_KW)


# Ответы-роутинг по насия/кредитному расчёту
NASIYA_ROUTING_MSG = {
    "ru": (
        "Точный расчёт ежемесячного платежа делает наш менеджер — "
        "он учтёт модель, первый взнос и срок.\n"
        "Напишите @Deepaluz — рассчитает за пару минут."
    ),
    "uz": (
        "Oylik to'lovni aniq hisoblash menejerlari tomonidan amalga oshiriladi — "
        "u model, boshlang'ich to'lov va muddatni hisobga oladi.\n"
        "@Deepaluz ga yozing — bir necha daqiqada hisob-kitob qiladi."
    ),
    "en": (
        "Exact monthly payment calculation is handled by our manager — "
        "they'll factor in the model, down payment, and term.\n"
        "Message @Deepaluz and they'll calculate it in minutes."
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# POST-PURCHASE DETECTION  — клиент уже купил и нуждается в поддержке менеджера
# ══════════════════════════════════════════════════════════════════════════════

# Явные признаки владения автомобилем (только у текущих владельцев)
_POST_PURCHASE_OWNERSHIP_KW = [
    # Русский — явное владение
    "мой voyah", "мой м-хиро", "мой mhero", "мой автомобиль уже",
    "моя машина уже", "у меня уже есть", "я уже владелец",
    "взял у вас", "купил у вас", "купила у вас",
    # Специфика после сделки (только релевантна после покупки)
    "пдт готов", "пдт готова", "когда пдт", "пдт документ",
    "гос номера готовы", "гос номер готов", "когда номера",
    "техпаспорт готов", "тех паспорт готов",
    "гарантийный талон", "сервисная книжка",
    # Узбекский
    "mening voyah", "mening avto", "mening mashinam",
    "raqam tayyor", "texpassport tayyor",
    "kafolat talonі", "kafolat taloni",
    "menинг voyah", "менинг авто",
]

# Слова поддержки / сервиса после покупки
_POST_PURCHASE_SUPPORT_KW = [
    # Ремонт и гарантия
    "гарантийный ремонт", "по гарантии", "гарантийный случай",
    "в сервис везти", "сервисный центр", "тех обслуживание",
    "техническое обслуживание", "то сделать",
    "зарядка не работает", "зарядное не работает",
    "зарядка сломалась", "не заряжается",
    "батарея не работает", "проблема с батареей",
    "экран не работает", "ошибка на экране",
    # Доставка (после оплаты)
    "когда доставят", "когда привезут машину", "когда заберу машину",
    "доставка машины", "привезли машину",
    # Документы (после сделки)
    "постановка на учёт", "поставить на учёт",
    "страховка", "осаго", "каско",
    # Uzbek
    "kafolatga topshirish", "texnik xizmat ko'rsatish",
    "zaryadka ishlamayapti", "batareya muammo",
    "mashinani qachon olib kelishadi", "yetkazib berish",
    "ro'yxatga olish", "sug'urta",
]


def detect_post_purchase(text: str, info: dict = None) -> bool:
    """
    True если клиент уже купил автомобиль и задаёт вопросы поддержки/сервиса.
    Такие запросы должны идти к живому менеджеру, а не к ИИ.

    Логика двухуровневая:
      1. Если purchased=True в БД → любой сервисный запрос = True
      2. Если в тексте явный признак владения + сервисный вопрос = True
    """
    if info is None:
        info = {}
    t = text.lower()

    # Уровень 1: клиент помечен как купивший → проверяем поддержку
    if info.get("purchased"):
        return any(kw in t for kw in _POST_PURCHASE_SUPPORT_KW)

    # Уровень 2: явный сигнал владения в тексте
    has_ownership = any(kw in t for kw in _POST_PURCHASE_OWNERSHIP_KW)
    if has_ownership:
        # + хоть один сервисный запрос → однозначно пост-продажа
        has_support = any(kw in t for kw in _POST_PURCHASE_SUPPORT_KW)
        return has_support

    # Уровень 3: специфика ТОЛЬКО после покупки (без доп. контекста)
    _definitive_post_purchase = [
        "пдт готов", "пдт готова", "когда пдт",
        "гос номера готовы", "гос номер готов", "когда номера",
        "техпаспорт готов", "гарантийный ремонт",
        "в сервис везти", "по гарантийному",
        "зарядка не работает", "зарядное не работает",
        "kafolatga topshirish", "zaryadka ishlamayapti",
    ]
    return any(kw in t for kw in _definitive_post_purchase)


# ══════════════════════════════════════════════════════════════════════════════
# GENDER DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_MALE_NAMES = {
    # Узбекские мужские
    "jasur", "bobur", "sherzod", "ulugbek", "nodir", "farrux", "akbar", "eldor",
    "doniyor", "sardor", "sanjar", "dilshod", "otabek", "bekzod", "jahongir",
    "javohir", "humoyun", "ibrohim", "islom", "kamol", "laziz", "mansur",
    "mirzo", "murod", "nurbek", "odil", "oybek", "ravshan", "rustam",
    "shuhrat", "suxrob", "temur", "umid", "vohid", "zafar", "zohid",
    "aziz", "utkir", "alisher", "anvar", "behruz", "davron", "firdavs",
    "жасур", "бобур", "шерзод", "улугбек", "нодир", "фаррух", "акбар",
    "дониёр", "сардор", "санжар", "дилшод", "отабек", "бекзод",
    "жавоҳир", "иброҳим", "ислом", "камол", "лазиз", "мансур",
    "мирзо", "мурод", "нурбек", "одил", "ойбек", "равшан", "рустам",
    "шуҳрат", "темур", "умид", "зафар", "азиз", "уткир",
    # Русские мужские
    "алексей", "дмитрий", "иван", "михаил", "сергей", "андрей", "николай",
    "владимир", "александр", "максим", "артём", "артем", "кирилл", "денис",
    "павел", "евгений", "роман", "олег",
}

_FEMALE_NAMES = {
    # Узбекские женские
    "malika", "nilufar", "zulfiya", "dilnoza", "nargiza", "mohira", "barno",
    "gulnora", "shahnoza", "feruza", "nasiba", "nodira", "oydin",
    "sarvinoz", "sevinch", "shahlo", "umida", "yulduz", "zuhra", "aziza",
    "kamola", "kamilla",
    "малика", "нилуфар", "зулфия", "дилноза", "наргиза", "моҳира", "барно",
    "гулнора", "шаҳноза", "феруза", "насиба", "нодира", "ойдин",
    "сарвиноз", "севинч", "шаҳло", "умида", "юлдуз", "зуҳра", "азиза",
    "камола", "камилла",
    # Русские женские
    "анна", "мария", "елена", "ольга", "наталья", "татьяна", "ирина",
    "светлана", "юлия", "екатерина", "алина", "виктория", "дарья", "ксения",
    "наталия", "людмила", "галина",
}


def detect_gender(name: str) -> str | None:
    """Возвращает 'male', 'female' или None."""
    if not name or name == "Клиент":
        return None
    first = name.strip().split()[0].lower()
    if first in _MALE_NAMES:
        return "male"
    if first in _FEMALE_NAMES:
        return "female"
    # Эвристика: окончания
    if first.endswith(("а", "я")) and not first.endswith(("ка", "жа")):
        return "female"
    if first.endswith(("бек", "жон", "хон", "мир", "зод", "ёр")):
        return "male"
    if first.endswith(("ой", "гул", "биби", "нисо")):
        return "female"
    return None


_HONORIFICS = {
    "male":   {"uz": "ака",     "ru": "уважаемый", "en": "sir"},
    "female": {"uz": "опа",     "ru": "уважаемая", "en": "ma'am"},
    None:     {"uz": "ака/опа", "ru": "",          "en": ""},
}


# ══════════════════════════════════════════════════════════════════════════════
# CLIENT MEMORY CONTEXT
# ══════════════════════════════════════════════════════════════════════════════

_FACT_LABELS = {
    "model":      "Интересуется моделью",
    "budget":     "Бюджет",
    "concern":    "Главное возражение",
    "competitor": "Сравнивал с",
    "timeline":   "Когда планирует купить",
    "payment":    "Способ оплаты",
    "family":     "Семья / кол-во мест",
}


def _build_memory_context(chat_id: int) -> str:
    """Формирует блок контекста из долгосрочной памяти о клиенте."""
    if not chat_id:
        return ""
    facts = load_client_facts(chat_id)
    if not facts:
        return ""
    lines = ["[ПАМЯТЬ О КЛИЕНТЕ (из прошлых разговоров):"]
    for key, label in _FACT_LABELS.items():
        val = facts.get(key)
        if val:
            lines.append(f"  • {label}: {val}")
    lines.append("Учитывай эти факты. Не спрашивай заново то что уже знаешь.]")
    return "\n".join(lines)


def _load_training_context(query: str) -> str:
    """
    Ищет в базе знаний Q&A пары релевантные текущему запросу.
    Возвращает форматированный блок для системного промпта.
    Вызывается синхронно (чтение из SQLite быстрое).
    """
    if not query or len(query.strip()) < 4:
        return ""
    try:
        pairs = search_training_qa(query, limit=2)
    except Exception:
        return ""
    if not pairs:
        return ""
    lines = ["[БАЗА ЗНАНИЙ — используй эти ответы как основу, адаптируй под тон разговора:"]
    for p in pairs:
        lines.append(f"В: {p['question']}")
        lines.append(f"О: {p['answer']}")
    lines.append("]")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_BASE = """\
Ты — Воя, менеджер по продажам автосалона TAT AUTO в Ташкенте.
Продаёшь электромобили Voyah и M-Hero. Своё имя — Воя.
Никогда не называй себя ИИ, ботом, роботом или виртуальным ассистентом — ты живой менеджер.

{lang_instruction}

СТИЛЬ — СТРОГО:
Пиши как живой человек в мессенджере. Без маркеров, списков, звёздочек и форматирования.
Максимум 2-3 предложения за раз. Коротко и по делу.
Обращайся на «Вы», уважительно.{name_instruction}
Пунктуация: один знак в конце. Никогда «?!» или «!!» — это грубо.
Не начинай с шаблонов: «Конечно!», «Отлично!», «Разумеется!», «Конечно же!».

{greeting_instruction}

ПРАВИЛО ПОСЛЕ ПОКУПКИ — АБСОЛЮТНОЕ:
Если клиент сообщает что УЖЕ КУПИЛ автомобиль у нас и задаёт вопросы про
гарантию, сервис, ремонт, документы, учёт, доставку, зарядку или поломку —
ответь ТОЛЬКО этой единственной фразой: «Передал ваш вопрос менеджеру.»
Ничего больше. Не объясняй. Не продавай. Не консультируй по сервису.

СТРАТЕГИЯ КОНСУЛЬТАНТА — потребности прежде всего:
Работай как грамотный консультант: сначала выясни потребности клиента, потом предлагай решение.
Каждый ответ двигает клиента к следующему шагу:
  Шаг 1 → Выяснить потребность (задай ОДИН квалифицирующий вопрос)
  Шаг 2 → Подобрать модель под потребность клиента
  Шаг 3 → Предложить тест-драйв — ТОЛЬКО если модель уже известна и диалог идёт давно
  Шаг 4 → Предложить кредит / насия если возражение по цене
  Шаг 5 → Закрыть — предложить оформить прямо сейчас

ТЕСТ-ДРАЙВ — строгие правила:
Не предлагай тест-драйв в первых 1-3 сообщениях диалога.
Не предлагай тест-драйв пока не знаешь какая модель интересует клиента.
Если клиент сам просит тест-драйв — немедленно записывай.

Квалифицирующие вопросы (задавай по ОДНОМУ, не списком):
  — «Какая модель интересует больше — Courage или Free?»
  — «Примерный бюджет рассматриваете?»
  — «Наличными или в кредит планируете?»
  — «На когда смотрите покупку — в этом месяце?»
  — «Есть машина на обмен?»

После ответа на вопрос клиента заканчивай ОДНИМ мягким шагом:
  Если модель неизвестна → спроси «Courage или Free?».
  Если клиент горячий (готов купить) → предложи оформить прямо сейчас.
  Если возражение по цене → объясни ценность, предложи кредитный расчёт через @Deepaluz.
  Если модель известна и клиент расспрашивал про характеристики → можно предложить тест-драйв.

ЧТО МЫ ПРОДАЁМ:
{prices}

{training_context}

КОНТАКТЫ: г. Ташкент, ул. Шота Руставели 77 · тел. +998 98 444 05 44

ПРАВИЛА:
Цену называй сразу — без уклонений и отговорок.
Никогда: «подождите», «сейчас узнаю», «уточню у менеджера», «проверю».
РАССРОЧКА:
Классической рассрочки (0% без переплаты) в TAT AUTO нет.
Если спрашивают рассрочку — скажи честно и предложи оба варианта:
1. Насия савдо — только M-Hero M817 и Voyah Free 318. Взнос 30%, до 36 месяцев.
2. Банковский кредит OFB — на все модели. Взнос от 25%, до 60 месяцев, от 21,9%.
Никогда не называй насия савдо «рассрочкой» — это разные вещи.
Насия савдо НЕТ на: Voyah Courage, Free+, Taishan.
OFB «Автокредит Лёгкий»: до 800 млн сум, от 12 до 60 мес., взнос от 25%.
Ставка: 25% взнос → 24,5%; 30% → 23,9%; 40% → 22,9%; 50% → 21,9%.
Называй конкретную ставку исходя из взноса — не говори «уточните условия».
При взносе 40%+ нужна выписка по карте за 6 мес. Паспорт + договор покупки.
РАСЧЁТ ПЛАТЕЖА: Никогда не вычисляй ежемесячный платёж самостоятельно. Если клиент просит «сколько в месяц» — скажи что точный расчёт делает менеджер и направь к @Deepaluz.
Адрес спросили — дай текстом (карту отправит система автоматически).
Фото попросили — скажи «сейчас отправлю» (фото отправит система).
Прайс-лист отправляется клиенту напрямую — никогда не упоминай канал, источник или откуда взят прайс.
После отправки прайса задай ОДИН уточняющий вопрос чтобы понять интерес клиента:
  — если модель неизвестна: «Какая модель больше интересует — Courage или Free?»
  — если модель известна: «Бюджет примерно рассматриваете?» или «Наличными или в кредит?»
Не дави на клиента. Если отказывается — прими спокойно и без обид.\
{extra}"""

_LANG_INSTRUCTIONS = {
    "uz": (
        "\nКлиент пишет по-узбекски. Отвечай на том же языке и той же письменностью: "
        "кириллицей если пишет кириллицей, латиницей если латиницей. "
        "Пиши грамотно, как образованный носитель узбекского языка. "
        "Если клиент переключится на русский — переключись и ты."
    ),
    "ru": (
        "\nКлиент пишет по-русски. Отвечай по-русски. "
        "Если клиент напишет на другом языке — отвечай на его языке."
    ),
    "en": (
        "\nThe client writes in English. Reply in English. "
        "If they switch language, follow their lead."
    ),
}

_GREETING_NEW = (
    "Это первое сообщение от клиента. "
    "Если он поздоровался — НАЧНИ свой ответ тем же приветствием: "
    "«Assalomu alaykum» → начни с «Assalomu alaykum!», "
    "«Привет» / «Здравствуйте» → начни с «Здравствуйте!». "
    "После приветствия кратко представься и спроси чем можешь помочь."
)

_GREETING_RETURNING = (
    "Разговор уже идёт — не приветствуй заново. Просто отвечай на вопрос."
)


def _build_system(chat_id: int = None, name: str = "", lang: str = "ru",
                   extra_context: str = "", has_history: bool = False,
                   name_known: bool = True, gender: str | None = None,
                   user_query: str = "") -> str:
    """
    Строит системный промпт для текущего диалога.
    user_query — последнее сообщение клиента (используется для поиска в базе знаний).
    """
    prices     = load_prices()
    lang_instr = _LANG_INSTRUCTIONS.get(lang, _LANG_INSTRUCTIONS["ru"])
    is_new     = not has_history

    # Инструкция по имени/обращению
    if name and name != "Клиент":
        name_instruction = f" Клиента зовут {name} — обращайся по имени."
    else:
        honorific = _HONORIFICS.get(gender, _HONORIFICS[None]).get(lang, "")
        if honorific and "/" not in honorific:
            name_instruction = f" Имя неизвестно — обращайся «{honorific}»."
        else:
            name_instruction = ""

    # Инструкция по приветствию
    greeting_instruction = _GREETING_NEW if is_new else _GREETING_RETURNING

    # База знаний — релевантные Q&A для текущего запроса клиента
    training_context = _load_training_context(user_query) if user_query else ""

    # Дополнительный контекст
    extra_parts = []

    if not name_known and is_new:
        extra_parts.append(
            "\n[В конце этого сообщения ненавязчиво спроси имя клиента — один раз.]"
        )

    if chat_id:
        # Долгосрочная память — конкретные факты о клиенте
        memory_ctx = _build_memory_context(chat_id)
        if memory_ctx:
            extra_parts.append(f"\n{memory_ctx}")
        # Краткое резюме последних разговоров
        summary = load_summary(chat_id)
        if summary:
            extra_parts.append(f"\n[Краткое резюме прошлых разговоров: {summary}]")

    if extra_context:
        extra_parts.append(f"\n{extra_context}")

    extra = "".join(extra_parts)

    return _SYSTEM_BASE.format(
        prices=prices,
        training_context=training_context,
        lang_instruction=lang_instr,
        name_instruction=name_instruction,
        greeting_instruction=greeting_instruction,
        extra=extra,
    )


# Приветственные фразы для удаления у вернувшихся клиентов
_GREETING_PREFIXES_LC = [
    "добро пожаловать в tat auto!",
    "добро пожаловать!",
    "рады приветствовать вас!",
    "здравствуйте!",
    "здравствуйте,",
    "добрый день!",
    "добрый вечер!",
    "доброе утро!",
    "xush kelibsiz!",
    "assalomu alaykum!",
    "ассалому алайкум!",
    "hello!",
    "welcome to tat auto!",
    "welcome!",
    "привет!",
]


def _strip_greeting(text: str) -> str:
    """Убирает стандартное приветствие из начала ответа для вернувшихся клиентов."""
    tl = text.lower()
    for phrase in _GREETING_PREFIXES_LC:
        if tl.startswith(phrase):
            result = text[len(phrase):].lstrip(" ,!\n")
            return result if result else text
    return text


# ══════════════════════════════════════════════════════════════════════════════
# AI REPLY
# ══════════════════════════════════════════════════════════════════════════════

async def get_ai_reply(
    messages:      list,
    chat_id:       int       = None,
    name:          str       = "",
    lang:          str       = "ru",
    extra_context: str       = "",
    is_new_client: bool      = True,
    name_known:    bool      = True,
    gender:        str|None  = None,
) -> str | None:

    # Последнее сообщение клиента — для поиска в базе знаний
    _user_query = ""
    for m in reversed(messages[-5:]):
        if m.get("role") == "user":
            _user_query = m.get("content", "")[:300]
            break

    system = _build_system(
        chat_id, name, lang, extra_context,
        has_history=not is_new_client,
        name_known=name_known,
        gender=gender,
        user_query=_user_query,
    )

    def _call():
        return _ai.messages.create(
            model="claude-opus-4-5",
            max_tokens=320,
            system=system,
            messages=messages[-12:],
        )

    for attempt in range(2):
        try:
            resp = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, _call),
                timeout=60.0,
            )
            text = resp.content[0].text.strip()
            # Убираем приветствие у вернувшихся клиентов
            if not is_new_client:
                text = _strip_greeting(text)
            return text

        except asyncio.TimeoutError:
            log.warning("AI timeout (попытка %d/2) chat_id=%s", attempt + 1, chat_id)

        except Exception as e:
            err = str(e).lower()
            if "rate_limit" in err or "overloaded" in err:
                log.warning("AI rate_limit/overloaded, жду 5 сек (chat_id=%s)...", chat_id)
                await asyncio.sleep(5)
            elif "invalid_api_key" in err or "authentication" in err:
                log.error("AI: неверный API-ключ! Проверь ANTHROPIC_API_KEY в .env")
                break
            else:
                log.exception("AI ошибка (chat_id=%s): %s", chat_id, e)
                break

    log.warning("AI не вернул ответ для chat_id=%s", chat_id)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

async def generate_summary(messages: list) -> str:
    if len(messages) < 6:
        return ""
    prompt = (
        "Сделай краткое резюме диалога (2-3 предложения): "
        "какой автомобиль интересует клиента, что спрашивал, на каком этапе."
    )
    req = messages[-30:] + [{"role": "user", "content": prompt}]
    try:
        resp = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _ai.messages.create(
                model="claude-haiku-4-5",
                max_tokens=200,
                messages=req,
            ),
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.warning("Ошибка summary: %s", e)
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# FACT EXTRACTION  (долгосрочная память)
# ══════════════════════════════════════════════════════════════════════════════

async def extract_facts(messages: list, chat_id: int):
    """
    Извлекает структурированные факты о клиенте через Claude Haiku.
    Вызывается в фоне — ошибки не влияют на основной флоу.
    Анализирует только сообщения клиента (role=user).
    """
    user_msgs = [m for m in messages[-16:] if m.get("role") == "user"]
    if len(user_msgs) < 2:
        return

    prompt = (
        "Из сообщений клиента ниже извлеки факты в JSON. "
        "Верни ТОЛЬКО валидный JSON без пояснений. "
        "Используй null если информации нет:\n"
        '{"model":"название модели авто или null",'
        '"budget":"бюджет числом в сумах или null",'
        '"concern":"главное возражение или вопрос клиента или null",'
        '"competitor":"конкурент которого упомянул или null",'
        '"timeline":"когда планирует купить или null",'
        '"payment":"кредит/наличные/насия или null",'
        '"family":"кол-во мест или размер семьи или null"}\n\n'
        "Сообщения клиента:\n"
        + "\n".join(f"- {m['content'][:250]}" for m in user_msgs)
    )

    try:
        resp = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _ai.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=180,
                    messages=[{"role": "user", "content": prompt}],
                ),
            ),
            timeout=20.0,
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json\n").strip()
        facts = json.loads(raw)
        facts = {k: v for k, v in facts.items()
                 if v and str(v).lower() not in ("null", "none", "")}
        if facts:
            save_client_facts(chat_id, facts)
            log.debug("Факты обновлены [%d]: %s", chat_id, facts)
    except json.JSONDecodeError as e:
        log.warning("extract_facts: невалидный JSON (chat_id=%d): %s", chat_id, e)
    except asyncio.TimeoutError:
        log.warning("extract_facts: timeout (chat_id=%d)", chat_id)
    except Exception as e:
        log.warning("extract_facts: %s (chat_id=%d)", e, chat_id)


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION LEARNING  (извлечение паттернов из успешных разговоров)
# ══════════════════════════════════════════════════════════════════════════════

async def extract_conversation_patterns(messages: list, chat_id: int) -> int:
    """
    Анализирует успешный диалог и извлекает Q&A паттерны в базу знаний.
    Вызывается менеджером через команду /импорт.
    Возвращает количество добавленных паттернов.

    Логика: берём только реальные пары клиент→менеджер из диалога,
    просим Haiku выделить лучшие как обучающие примеры.
    Фильтруем: не учимся на системных сообщениях и fallback-ответах.
    """
    # Фильтруем только текстовые сообщения с содержательным контентом
    clean_msgs = [
        m for m in messages
        if m.get("content") and len(m["content"]) > 10
        and "[Прайс" not in m["content"]
        and "fallback" not in m["content"].lower()
    ]
    if len(clean_msgs) < 4:
        return 0

    convo_text = "\n".join(
        f"{'Клиент' if m['role'] == 'user' else 'Менеджер'}: {m['content'][:300]}"
        for m in clean_msgs[-40:]
    )

    prompt = (
        "Это успешный диалог продаж автомобилей. "
        "Извлеки 3-6 лучших пар «вопрос клиента → ответ менеджера», "
        "которые показывают правильную технику продаж: работу с возражениями, "
        "объяснение ценности, закрытие сделки.\n"
        "Верни ТОЛЬКО валидный JSON-массив без пояснений:\n"
        '[{"q": "вопрос клиента", "a": "ответ менеджера"}, ...]\n\n'
        "НЕ включай: приветствия, запросы цены/фото, системные сообщения.\n"
        "Включай ТОЛЬКО продающие техники и работу с возражениями.\n\n"
        f"Диалог:\n{convo_text}"
    )

    try:
        resp = await asyncio.wait_for(
            asyncio.get_running_loop().run_in_executor(
                None,
                lambda: _ai.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=600,
                    messages=[{"role": "user", "content": prompt}],
                ),
            ),
            timeout=30.0,
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json\n").strip()
        patterns = json.loads(raw)
        if not isinstance(patterns, list):
            return 0

        count = 0
        for p in patterns:
            q = str(p.get("q", "")).strip()
            a = str(p.get("a", "")).strip()
            if len(q) > 10 and len(a) > 10:
                save_training_qa(q, a, source=f"conv_{chat_id}")
                count += 1
        log.info("extract_conversation_patterns: %d паттернов из чата %d", count, chat_id)
        return count

    except json.JSONDecodeError as e:
        log.warning("extract_conversation_patterns: невалидный JSON (chat_id=%d): %s", chat_id, e)
    except asyncio.TimeoutError:
        log.warning("extract_conversation_patterns: timeout (chat_id=%d)", chat_id)
    except Exception as e:
        log.warning("extract_conversation_patterns: %s (chat_id=%d)", e, chat_id)
    return 0


# ══════════════════════════════════════════════════════════════════════════════
# INTENT / CONTENT DETECTORS
# ══════════════════════════════════════════════════════════════════════════════

def is_asking_price(text: str) -> bool:
    kw = [
        "прайс", "цена", "цены", "сколько стоит", "стоимость", "почём",
        "нарх", "нархи", "канча", "qancha turadi", "qancha", "narxi",
        "price", "cost", "how much", "прайслист", "price list", "нархлар",
    ]
    return any(k in text.lower() for k in kw)


def is_asking_location(text: str) -> bool:
    kw = [
        "локация", "адрес", "где находитесь", "где вы", "как добраться",
        "местонахождение", "location", "address", "where are you", "how to get",
        "manzil", "qayerda", "qayer", "joylashuv", "манзил", "қаерда",
    ]
    return any(k in text.lower() for k in kw)


def is_asking_photo(text: str) -> bool:
    kw = [
        "фото", "фотки", "фотографии", "покажи", "как выглядит",
        "внешний вид", "photo", "picture", "show me",
        "rasm", "ko'rsat", "rasmlar", "расм", "кўрсат",
    ]
    return any(k in text.lower() for k in kw)


def get_all_models_from_text(text: str) -> list[str]:
    """Возвращает ВСЕ модели упомянутые в тексте (без дублей).

    Правила приоритета (порядок важен из-за подстрочных совпадений):
      free+ → free_plus (до free чтобы не захватить «free» внутри «free+»)
      free 318 / 318 → free_318
      free / фри (без +/318) → free_318 (дефолт)
    """
    t = text.lower()
    found: list[str] = []

    if "taishan" in t or "тайшань" in t or "тайшан" in t:
        found.append("taishan")
    if "courage" in t or "куреж" in t or "кураж" in t or "карадж" in t or "кариж" in t:
        found.append("courage")
    if "m817" in t or "m-hero" in t or "м817" in t or "м-херо" in t or "mhero" in t:
        found.append("m817")

    # free_plus до free_318 — иначе «free+» захватит оба
    if "free+" in t or "free plus" in t or "фри+" in t or "фри плюс" in t:
        found.append("free_plus")
    elif "free 318" in t or "фри 318" in t or (
            "318" in t and "free" not in t.replace("318", "")):
        # «318» без «free» рядом (напр. «Voyah 318»)
        found.append("free_318")
    elif "free" in t or "фри" in t:
        # Просто «free» — подразумеваем free_318 как более популярную
        found.append("free_318")

    return found


def get_model_from_text(text: str) -> str | None:
    """Возвращает первую найденную модель (обратно совместима с прежней сигнатурой)."""
    models = get_all_models_from_text(text)
    return models[0] if models else None
