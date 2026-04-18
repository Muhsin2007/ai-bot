"""
AI handler — Claude API, language detection, intent detection.
"""
import asyncio
import logging
import os
import random

import anthropic
from dotenv import load_dotenv

from storage_db import load_prices, load_summary

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
# SYSTEM PROMPT
# ══════════════════════════════════════════════════════════════════════════════

_SYSTEM_BASE = """\
Ты — менеджер по продажам автосалона TAT AUTO в Ташкенте. Продаёшь электромобили Voyah и M-Hero.

{lang_instruction}

СТИЛЬ — СТРОГО:
Пиши как живой человек в мессенджере. Без маркеров, списков, звёздочек и форматирования.
Максимум 2-3 предложения за раз. Коротко и по делу.
Обращайся на «Вы», уважительно.{name_instruction}
Пунктуация: один знак в конце. Никогда «?!» или «!!» — это грубо.
Не начинай с шаблонов: «Конечно!», «Отлично!», «Разумеется!», «Конечно же!».
Никогда не говори что ты ИИ или робот.

{greeting_instruction}

ЧТО МЫ ПРОДАЁМ:
{prices}

КОНТАКТЫ: г. Ташкент, ул. Шота Руставели 77 · тел. +998 95 004 97 49

ПРАВИЛА:
Цену называй сразу — без уклонений и отговорок.
Никогда: «подождите», «сейчас узнаю», «уточню у менеджера», «проверю».
РАССРОЧКА (rasrochka):
Классической рассрочки (0% без переплаты) в TAT AUTO пока нет и неизвестно когда будет.
Если клиент просит рассрочку — скажи честно: «Рассрочки пока нет, но есть два удобных варианта» и предложи оба:
1. Насия савдо — доступно только на M-Hero M817 и Voyah Free 318 (есть счёт-справка). Взнос 30%, срок до 36 месяцев.
2. Банковский кредит OFB — на все модели. Взнос от 25%, срок до 60 месяцев, ставка от 21,9%.
Никогда не называй насия савдо «рассрочкой» — это разные вещи.
Насия савдо доступно ТОЛЬКО для автомобилей со счёт-справкой: M-Hero M817 и Voyah Free 318. На Voyah Courage, Voyah Free+ и Voyah Taishan насия савдо НЕТ.
Если клиент интересуется M-Hero M817 или Voyah Free 318 — обязательно упомяни что на эти модели есть счёт-справка и доступно насия савдо.
Банковский кредит (OFB «Автокредит Лёгкий»): сумма до 800 млн сум, срок от 12 до 60 месяцев, первый взнос от 25%. Ставка зависит от взноса: 25% → 24,5% годовых, 30% → 23,9%, 40% → 22,9%, 50% → 21,9%. Нужны: паспорт + договор покупки. При взносе от 40% — дополнительно выписка по карте за 6 месяцев. Ежемесячный платёж без комиссии.
Когда клиент спрашивает про кредит — называй конкретную ставку исходя из его взноса, не говори просто «уточните условия».
Адрес спросили — дай текстом (карту отправит система автоматически).
Фото попросили — скажи «сейчас отправлю» (фото отправит система).
Не дави на клиента. Если отказывается — прими спокойно и без обид.
Если клиент говорит что уже купил или просит не писать — вежливо попрощайся одним предложением.\
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
                   name_known: bool = True, gender: str | None = None) -> str:

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

    # Дополнительный контекст
    extra_parts = []

    if not name_known and is_new:
        extra_parts.append(
            "\n[В конце этого сообщения ненавязчиво спроси имя клиента — один раз.]"
        )

    if chat_id:
        summary = load_summary(chat_id)
        if summary:
            extra_parts.append(f"\n[Краткое резюме прошлых разговоров: {summary}]")

    if extra_context:
        extra_parts.append(f"\n{extra_context}")

    extra = "".join(extra_parts)

    return _SYSTEM_BASE.format(
        prices=prices,
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

    system = _build_system(
        chat_id, name, lang, extra_context,
        has_history=not is_new_client,
        name_known=name_known,
        gender=gender,
    )

    def _call():
        return _ai.messages.create(
            model="claude-opus-4-5",
            max_tokens=400,
            system=system,
            messages=messages[-20:],
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
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=req,
            ),
        )
        return resp.content[0].text.strip()
    except Exception as e:
        log.warning("Ошибка summary: %s", e)
        return ""


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


def get_model_from_text(text: str) -> str | None:
    t = text.lower()
    if "taishan" in t or "тайшань" in t or "тайшан" in t:
        return "taishan"
    if "courage" in t or "куреж" in t or "кураж" in t:
        return "courage"
    if "m817" in t or "m-hero" in t or "м817" in t or "м-херо" in t:
        return "m817"
    if "free+" in t or "free plus" in t or "фри+" in t or "фри плюс" in t:
        return "free_plus"
    if "free 318" in t or "318" in t or "фри 318" in t:
        return "free_318"
    if "free" in t or "фри" in t:
        return "free_318"
    return None
