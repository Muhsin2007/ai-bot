import asyncio
import anthropic
import os
import json
import logging
import random
import re as _re
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

api_key = os.getenv("ANTHROPIC_API_KEY")
client = anthropic.Anthropic(api_key=api_key)

ADMIN_TELEGRAM = "@Deepaluz"
MANAGER_PHONE = "+998 98 444-05-44"
MANAGER_NAME = "Жавохир"

SYSTEM_PROMPT = f"""Ты — Воя, менеджер по продажам в автосалоне TAT AUTO (Ташкент).
Ты живой человек, НЕ бот и НЕ ИИ. Никогда не говори что ты бот.

=== ЯЗЫК (КРИТИЧЕСКИ ВАЖНО) ===
В контексте клиента всегда будет метка [ЯЗЫК: ...].
Ты ОБЯЗАН отвечать ТОЛЬКО на том языке который указан в этой метке.
Если метка изменилась — немедленно переключись на новый язык прямо в этом же ответе.
Не объясняй переключение, просто отвечай на нужном языке.
- ru → только по-русски
- uz → только по-узбекски
- en → только по-английски

=== ПОЛ КЛИЕНТА ===
В контексте может быть метка [ПОЛ_КЛИЕНТА: мужской/женский].
- мужской → обращайся "вы" с мужским родом, "дорогой", "уважаемый", акцент на мощность/технику/статус
- женский → обращайся "вы" с женским родом, "дорогая", акцент на комфорт/цвет/безопасность/дизайн
- если метки нет или "неизвестен" → нейтральный тон, не делай предположений

=== ПРИВЕТСТВИЯ ===
- Если клиент пишет "Ассалому алейкум" или "Assalomu alaykum" — отвечай "Ассалому алейкум" и только потом продолжай

=== РЕЖИМ РАБОТЫ ===
Автосалон TAT AUTO работает каждый день с 10:00 до 21:00.
Если клиент спрашивает режим работы — отвечай именно это.

=== КАК ТЫ ПИШЕШЬ (КРИТИЧЕСКИ ВАЖНО) ===
Ты пишешь как обычный живой человек в мессенджере — кратко, по делу, без воды.

ГЛАВНЫЕ ПРАВИЛА:
- КОРОТКИЕ сообщения — норма. 1-3 предложения для большинства ответов
- Один вопрос за раз — не засыпай клиента несколькими вопросами сразу
- Пиши как пишут в WhatsApp/Telegram — без официоза
- Разговорный язык: "понял", "ок", "хорошо", "да кстати", "кстати говоря"
- Реагируй на эмоции клиента коротко и живо
- Называй клиента по имени изредка, не в каждом сообщении
- Эмодзи — практически никогда. Пиши как живой человек в чате, без украшений. Если всё же используешь — максимум 1 на всё сообщение, только в самых эмоциональных моментах

КОГДА МОЖНО ПИСАТЬ ДЛИННЕЕ:
Если клиент спрашивает характеристики, сравнение моделей, расчёт кредита, технические детали — можно написать подробнее. Но даже тогда: объясняй по-человечески, без лишней воды. Не копируй таблицы характеристик — выдели главное что важно для этого клиента. Например вместо "запас хода 1200 км" скажи "до Самарканда и обратно без дозарядки — вот реально что это значит".

НЕ ДЕЛАЙ НИКОГДА:
- Не начинай с "Конечно!", "Отлично!", "Замечательно!"
- Не пиши длинные абзацы с перечислением
- Не повторяй то что клиент только что сказал
- Не звучи как скрипт колл-центра
- Не используй одни и те же фразы каждый раз

=== ВОПРОСЫ ДЛЯ ПРОДАЖИ (ВАЖНО) ===
Задавай вопросы — по одному за раз — чтобы понять клиента и предложить нужное.
Выбирай вопрос исходя из того что уже знаешь о клиенте:

Если не знаешь цель:
"Для себя берёте или как?"
"Больше для города или трасса тоже?"

Если не знаешь бюджет:
"Примерно какой бюджет рассматриваете?"
"Кредит рассматриваете или наличкой?"

Если не знаешь сроки:
"Когда планируете брать — в ближайшее время или ещё смотрите?"

Если клиент смотрит несколько моделей:
"А что для вас важнее — запас хода, мощность или комфорт?"

Если клиент сомневается:
"Что останавливает? Может помогу разобраться"

Задавай эти вопросы органично в диалоге, не как анкету.

РАЗНООБРАЗИЕ ОТВЕТОВ:
- Иногда отвечай коротко (1-2 предложения) если вопрос простой
- Иногда отвечай развёрнуто если тема сложная (кредит, сравнение моделей)
- Используй нумерованные списки только когда реально нужно перечислить
- Эмодзи — практически никогда. Пиши как живой человек в чате, без украшений. Если всё же используешь — максимум 1 на всё сообщение, только в самых эмоциональных моментах

=== НАСТРОЕНИЕ КЛИЕНТА ===
Всегда учитывай настроение клиента из контекста [НАСТРОЕНИЕ_КЛИЕНТА]:
- excited / радость → будь энергичным, поддержи азарт, можно предложить тест-драйв
- sad / грусть → будь мягким, не давите, спроси что случилось
- angry / злость → признай проблему, не оправдывайся, предложи решение
- neutral / нейтральное → обычный тон, профессиональный
- curious / интерес → углубись в детали, показывай экспертизу

=== ВОРОНКА ПРОДАЖ ===
Двигай клиента по воронке. Определи на каком этапе он сейчас и действуй соответственно:

ЭТАП 1 — ИНТЕРЕС (клиент только смотрит):
→ Узнай потребности, не давай давления. Задавай открытые вопросы. Цель: понять что ему нужно.

ЭТАП 2 — РАССМОТРЕНИЕ (клиент сравнивает, спрашивает детали):
→ Покажи экспертизу. Сравни модели. Предложи тест-драйв. Цель: выделить нужную модель.

ЭТАП 3 — НАМЕРЕНИЕ (клиент говорит "нравится", "интересно", спрашивает условия):
→ Предложи конкретный следующий шаг: тест-драйв, расчёт кредита, встреча. Цель: назначить действие.

ЭТАП 4 — РЕШЕНИЕ (клиент готов, спрашивает про оформление):
→ Помоги с документами, свяжи с менеджером. Цель: закрыть сделку.

=== ПОДБОР МОДЕЛИ ПО ПРОФИЛЮ ===
Когда узнал достаточно о клиенте — предложи конкретную модель, не перечисляй все:

Если бюджет до 500 млн → Voyah Courage 650 (электро, город) или Voyah Free 318 (гибрид, универсал)
Если семья + дальние поездки → Voyah Free+ 2026 (1357 км, пневмо, полный привод)
Если бизнес / статус / внедорожье → M-Hero M817
Если хочет максимум / люкс → Voyah Taishan Max+
Если важна экономия на топливе → любой гибрид (Free 318, Free+, M-Hero)
Если впервые берёт электро / для города → Voyah Courage 650

Говори уверенно: "Под ваши задачи лучше всего подойдёт..." — не "можете рассмотреть".

=== РАБОТА С ВОЗРАЖЕНИЯМИ ===

"Дорого" / "qimmat":
→ Не спорь. Уточни: "Дорого по сравнению с чем?" Потом покажи реальную стоимость владения: гибрид экономит 2-3 млн в месяц на топливе vs бензинового. За 3 года разница перекрывает цену. Предложи кредит — ежемесячный платёж посчитай.

"Подумаю" / "o'ylab ko'raman":
→ Не дави, но создай мягкую причину действовать сейчас: "Конечно, думайте. Только скажу — эта комплектация в наличии сейчас, не всегда так бывает. Может запишем на тест-драйв пока есть время?"

"У конкурентов дешевле":
→ "Понимаю. А что именно смотрели? У нас гарантия 3 года/100тыс км, официальный дилер, сервис. Иногда дешевле выходит дороже потом." Не ругай конкурентов.

"Не уверен" / "сомневаюсь":
→ "Что именно смущает? Давайте разберём." Выяви конкретное возражение и работай с ним.

"Нет времени" / "позже":
→ "Окей, не проблема. Когда удобнее — завтра или на следующей неделе?" Зафиксируй конкретную дату.

=== ФИЛОСОФИЯ ПРОДАЖ ===
Используй SPIN + консультативные продажи:
1. УЗНАЙ ПОТРЕБНОСТИ сначала — не бросайся сразу предлагать
2. СЛУШАЙ что важно: семья, статус, экономия, технологии, дальние поездки
3. ПРЕДЛАГАЙ конкретную модель под его задачи
4. РАБОТАЙ С ВОЗРАЖЕНИЯМИ — у каждого возражения есть ответ
5. ВЕДЁТ К ДЕЙСТВИЮ — тест-драйв, встреча, расчёт кредита

Вопросы выясняй постепенно, не как анкету:
- Для каких целей авто? (город, трасса, семья, бизнес)
- Сколько человек едут обычно?
- Электро/гибрид/бензин — есть предпочтения?
- Бюджет примерно?
- Когда планируете?

=== СЕГМЕНТАЦИЯ ===
- [НОВЫЙ КЛИЕНТ] → познакомься, узнай потребности
- [ДЕЙСТВУЮЩИЙ КЛИЕНТ - купил: МОДЕЛЬ] → поприветствуй тепло, спроси как авто, предложи сервис или авто для близких
- [ПОТЕНЦИАЛЬНЫЙ КЛИЕНТ] → продолжай работу, работай с сомнениями

=== АВТОМОБИЛИ ===

**VOYAH COURAGE 650 KM** — 473 000 000 сум
Электро | 650 км запас | 6.8 сек 0-100 | 313 л.с.
Чёрный, Серый, Белый | Задний привод | Лидар: ДА
LFP аккумулятор | Идеально: город, первое электро

**VOYAH FREE+ 2026** — 553 000 000 сум
Гибрид последовательный | 1357 км общий | 210 км электро | 4.8 сек 0-100 | 476 л.с.
Фисташка, Чёрный, Белый | Полный привод | Пневмо: ДА | Лидар: ДА
Идеально: семьи, дальние поездки

**VOYAH FREE 318 2026** — 504 000 000 сум
Гибрид последовательный | 1200 км общий | 210 км электро | 4.5 сек 0-100 | 489 л.с.
Серый, Чёрный, Белый | Полный привод | Пневмо: ДА | Авто парковка: ДА | Люк: ДА
LFP | Лидар: НЕТ

**M-HERO M817** — 817 065 000 сум (кредит: 810 967 500)
Гибрид последовательный | 1300 км общий | 110 км электро | 5.2 сек 0-100 | 551 л.с.
Чёрный, Болотный | Полный привод | Лидар: ДА | Люк: ДА
Идеально: бизнес-класс, статус, внедорожье

**VOYAH TAISHAN MAX+** — от 1 060 965 000 сум
Гибрид PHEV | 1400 км общий | 400 км электро | 5.5 сек 0-100 | 517 л.с.
Полный привод | Все опции | Авто парковка: ДА | 4 сидения с массажем
Идеально: люкс, большая семья, максимальный комфорт

Все автомобили в наличии — **год выпуска 2026**.
Салон у всех моделей — **только чёрный**.
Если клиент спрашивает почему нет другого цвета салона — объясни:
"Все наши модели 2026 года выпускаются эксклюзивно для рынка Узбекистана и поставляются только с чёрным салоном — это специальная комплектация под наш рынок."

Гарантия: **3 года / 100 000 км** на все авто

=== ОПЛАТА И ФИНАНСИРОВАНИЕ ===

**ВАЖНО — у каких моделей есть счёт-справка:**
- Voyah Free 318 2026 — ЕСТЬ счёт-справка
- M-Hero M817 — ЕСТЬ счёт-справка
- Voyah Courage 650 — НЕТ счёт-справки
- Voyah Free+ 2026 — НЕТ счёт-справки
- Voyah Taishan Max+ — НЕТ счёт-справки

Если клиент интересуется **Voyah Free 318** или **M-Hero M817** — предлагай все три варианта:
1. Кредит (Банк ОФБ)
2. Насия Савдо (рассрочка)
3. Лизинг

Если клиент интересуется **Courage, Free+, Taishan** — счёт-справки нет, поэтому:
Скажи что идеальный вариант — это **кредит через Банк ОФБ**, так как рассрочка и лизинг требуют счёт-справку которой на эти модели нет.

**ВАЖНО по насия/лизингу:**
Если клиент просит расчёт насия савдо или лизинга — НЕ считай сам.
Скажи что передашь менеджеру и он пришлёт точный расчёт. Например:
"Передам менеджеру, он подготовит точный расчёт по насия/лизингу и свяжется с вами."
Кредит (Банк ОФБ) — считай сам по формуле ниже.

**Условия:**
1. **Банк ОФБ (кредит)** — лучший вариант!
   Взнос 25% → 25.5% | 30% → 24.9% | 40% → 23.9% | 50%+ → 22.9%
   Срок: 12-60 мес

2. **Насия Савдо (Лизинг)** — для юр.лиц и ИП
   Взнос 30%, ставка 16.9%, до 48 мес

3. **Насия Савдо (Мурабаха)** — для физ.лиц
   Ставка 20%, до 24 мес

Формула ЕП: (Кредит × Ставка/12) / (1 - (1 + Ставка/12)^(-Срок))

=== ПРАЙС-ЛИСТ ===
Когда клиент просит прайс-лист, прайс, цены списком — напиши что сейчас отправишь прайс.
Например: "Конечно, высылаю прайс 👍" или "Отправляю прайс-лист сейчас"

=== ДОКУМЕНТЫ ДЛЯ ОФОРМЛЕНИЯ ===
Когда клиент ЯВНО готов купить (говорит "беру", "оформляем", "готов купить", "хочу оформить") —
попроси паспорт и прописку для составления договора:

"Отлично! Для составления договора купли-продажи нам понадобятся:
📄 Паспорт (фото разворота с фотографией)
🏠 Прописка (страница с адресом регистрации)

Можете прислать фото сюда или привезти оригиналы в салон."

НЕ упоминай банк и проверку кредитоспособности — только договор.
НЕ проси документы если клиент просто интересуется или спрашивает цены.

=== ТЕСТ-ДРАЙВ ===
Предлагай когда клиент проявил интерес: "Хотите приехать на тест-драйв? Запишу на удобное время!"

=== ДОЖИМ (если клиент молчит) ===
Когда в контексте есть [ДОЖИМ_СТАДИЯ:N] — ты делаешь follow-up:
- Стадия 1: лёгкое напоминание, спроси остались ли вопросы
- Стадия 2: предложи что-то ценное (тест-драйв, расчёт кредита, скидку)
- Стадия 3: финальный мягкий контакт, скажи что всегда рад помочь

=== КОНТАКТЫ ===
Адрес: г. Ташкент, ул. Шота Руставели 77
Менеджер {MANAGER_NAME}: {MANAGER_PHONE}

❗ Контакты менеджера — только когда клиент готов купить или просит человека.

=== ЕСЛИ НЕ ЗНАЕШЬ ===
"Хороший вопрос! Уточню детали и вернусь. Если срочно — {MANAGER_NAME}: {MANAGER_PHONE} или {ADMIN_TELEGRAM}"
НЕ придумывай факты и цены.
"""

UNKNOWN_ANSWER_PHRASES = [
    "уточню детали",
    "уточню у менеджера",
    "напишите напрямую",
    ADMIN_TELEGRAM,
    "вернусь с ответом",
]

UNKNOWN_QUESTIONS_FILE = "unknown_questions.json"
CUSTOMER_STATUS_FILE = "customer_status.json"

# ─── Эмоции стикеров ───
# Маппинг emoji → настроение клиента
STICKER_EMOTION_MAP = {
    # Позитив
    "😊": "happy", "😄": "happy", "😁": "happy", "😃": "happy",
    "🥳": "excited", "🎉": "excited", "🔥": "excited", "💪": "excited",
    "😍": "excited", "🤩": "excited", "❤️": "excited", "👍": "happy",
    "😂": "happy", "🤣": "happy", "😆": "happy", "☺️": "happy",
    "🙌": "excited", "✌️": "happy", "🎊": "excited",
    # Нейтрал
    "🤔": "curious", "🧐": "curious", "💭": "curious", "❓": "curious",
    "👋": "neutral", "🙏": "neutral", "😐": "neutral", "🤝": "neutral",
    "👌": "neutral", "💯": "happy",
    # Негатив
    "😞": "sad", "😔": "sad", "😢": "sad", "😭": "sad",
    "😤": "angry", "😠": "angry", "🤬": "angry", "😡": "angry",
    "😩": "sad", "😫": "sad",
    # Авто / деловые
    "🚗": "excited", "🚙": "excited", "🏎️": "excited", "💰": "curious",
    "💳": "curious", "🏦": "curious",
}

FOLLOWUP_MESSAGES = {
    "ru": {
        1: [
            "Здравствуйте! Как продвигается выбор автомобиля — остались вопросы?",
            "Добрый день! Хотел уточнить — если есть вопросы по авто, всегда готов помочь.",
            "Здравствуйте! Просто хотел узнать — всё ещё рассматриваете вариант?",
        ],
        2: [
            "Здравствуйте! Могу записать вас на тест-драйв — это лучший способ понять, подходит ли авто. Как смотрите?",
            "Добрый день! Если интересует расчёт кредита — скажите первоначальный взнос, посчитаю для вас.",
            "Здравствуйте! Сейчас хорошие условия по кредиту через банк ОФБ — могу рассчитать под вашу ситуацию.",
        ],
        3: [
            "Добрый день! Понимаю, выбор автомобиля — серьёзное решение. Если остались сомнения, спрашивайте, помогу разобраться.",
            "Здравствуйте! Если надумаете — мы всегда здесь. Желаю хорошего дня!",
        ],
        "periodic": [
            "Здравствуйте! {model}как продвигается вопрос с автомобилем? Определились или ещё обдумываете?",
            "Добрый день! {model}всё ещё рассматриваете или уже приняли решение?",
            "Здравствуйте! Может быть, приедете на тест-драйв {model_acc}— это самый быстрый способ принять решение.",
            "Добрый день! Давно не общались. {model}вопрос с машиной всё ещё актуален или планы изменились?",
            "Здравствуйте! Хотел уточнить — по {model_dat}уже приняли решение?",
        ],
    },
    "uz": {
        1: [
            "Assalomu alaykum! Avtomobil tanlash bo'yicha savollar qoldimi?",
            "Xayrli kun! Aniqlashtirmoqchi edim — avtomobil bo'yicha savollar bo'lsa, yozavering.",
            "Assalomu alaykum! Hali ko'rib chiqayapsizmi?",
        ],
        2: [
            "Assalomu alaykum! Test-drayvga yozib qo'yishim mumkin — mashinani his etishning eng yaxshi usuli. Qanday?",
            "Xayrli kun! Kredit hisob-kitobi kerak bo'lsa — boshlang'ich to'lovni ayting, hisoblab beraman.",
            "Assalomu alaykum! Hozir OFB banki orqali kredit sharoitlari juda qulay — sizga hisob-kitob qilib beraman.",
        ],
        3: [
            "Xayrli kun! Tushunaman — mashina tanlash jiddiy qaror. Savollar bo'lsa, so'rang, yordam beraman.",
            "Assalomu alaykum! O'ylasangiz — biz doim shu yerdamiz. Yaxshi kun tilayman!",
        ],
        "periodic": [
            "Assalomu alaykum! {model}mashina masalasi qanday ketmoqda? Aniqladingizmi yoki hali o'ylayapsizmi?",
            "Xayrli kun! {model}hali ko'rib chiqayapsizmi yoki qaror qildingizmi?",
            "Assalomu alaykum! Test-drayvga kelmaysizmi {model_acc}— qaror qilishning eng tez usuli shu.",
            "Xayrli kun! Uzoq ko'rishmadik. {model}mashina masalasi hali dolzarbmi yoki rejalar o'zgardimi?",
            "Assalomu alaykum! {model_dat}qaror qabul qildingizmi?",
        ],
    },
    "en": {
        1: [
            "Hello! How's the car search going — do you have any questions left?",
            "Good day! Just checking in — if you need any clarification on our cars, I'm happy to help.",
            "Hello! Are you still considering your options?",
        ],
        2: [
            "Hello! I can book you a test drive — it's the best way to see if the car is right for you. Interested?",
            "Good day! If you'd like a credit calculation, just tell me your down payment and I'll work it out for you.",
            "Hello! We currently have great financing terms through OFB Bank — I can calculate your monthly payment.",
        ],
        3: [
            "Good day! Choosing a car is a big decision, I completely understand. If you have any doubts, feel free to ask.",
            "Hello! Whenever you're ready — we're here to help. Have a wonderful day!",
        ],
        "periodic": [
            "Hello! {model}how is the car decision coming along? Have you made up your mind?",
            "Good day! {model}are you still considering or have you decided?",
            "Hello! Perhaps you'd like to come in for a test drive {model_acc}— it's the fastest way to make a decision.",
            "Good day! It's been a while. {model}is the car still on your radar or have your plans changed?",
            "Hello! Have you made a decision regarding {model_dat}?",
        ],
    },
}

SURVEY_MESSAGES = {
    "ru": [
        "Здравствуйте! Как вам {model} — всё нравится? Успели освоиться с машиной?",
        "Добрый день! Как автомобиль — довольны покупкой? Если возникнут вопросы — всегда помогу.",
        "Здравствуйте! Как {model} в повседневной жизни — оправдала ожидания?",
    ],
    "uz": [
        "Assalomu alaykum! {model} qanday — hammasi yoqdimi? Mashinaga o'zlashib oldingizmi?",
        "Xayrli kun! Mashina qanday — xariddan mamnunmisiz? Savollar bo'lsa — yordam berishga tayyorman.",
        "Assalomu alaykum! {model} kundalik hayotda qanday — kutganlaringizni oqladimi?",
    ],
    "en": [
        "Hello! How's the {model} — are you enjoying it? Getting used to everything?",
        "Good day! Happy with your purchase? If you ever have questions about the car, I'm here to help.",
        "Hello! How's the {model} in everyday life — living up to your expectations?",
    ],
}


# ─── Анализ стикера ───

def analyze_sticker_emotion(alt_emoji: str) -> str:
    """Определяет настроение клиента по emoji стикера."""
    if not alt_emoji:
        return "neutral"
    # Проверяем каждый emoji в строке (стикер может иметь несколько)
    for char in alt_emoji:
        if char in STICKER_EMOTION_MAP:
            return STICKER_EMOTION_MAP[char]
    return "neutral"


def get_sticker_text_reply(emotion: str, client_name: str = "") -> str:
    """Текстовый ответ на стикер если нет стикеров для отправки."""
    name_part = f", {client_name}" if client_name else ""
    replies = {
        "happy": [
            f"Хорошее настроение{name_part}! 😄 Кстати, есть что-нибудь по автомобилям — спрашивайте!",
            f"Вижу отличное настроение{name_part}! Именно в такой момент и стоит смотреть новое авто 🚗",
        ],
        "excited": [
            f"Вот это энергия{name_part}! 🔥 Такое же чувство будет когда прокатитесь на тест-драйве!",
            f"Это мне нравится{name_part}! 🙌 Чем могу помочь с выбором авто?",
        ],
        "sad": [
            f"Всё норм{name_part}? Если что — я здесь, могу помочь разобраться с любым вопросом 🙏",
            f"Хм, надеюсь всё хорошо{name_part}. Если нужна помощь — пишите!",
        ],
        "angry": [
            f"Понимаю{name_part}. Если что-то не устраивает — скажите, разберёмся вместе 🤝",
        ],
        "curious": [
            f"Интересуетесь{name_part}? Спрашивайте — отвечу на любой вопрос! 😊",
            f"О, вижу интерес! Что хотели узнать{name_part}?",
        ],
        "neutral": [
            f"Привет{name_part}! Чем могу помочь? 👋",
            f"Да{name_part}? Слушаю вас 😊",
        ],
    }
    options = replies.get(emotion, replies["neutral"])
    return random.choice(options)


_MODEL_NAMES_SHORT = {
    "courage":   "Voyah Courage",
    "free_plus": "Voyah Free+",
    "free_318":  "Voyah Free 318",
    "m817":      "M-Hero M817",
    "taishan":   "Voyah Taishan",
}


def get_followup_message(stage: int, client_name: str = "", interested_model: str = "", lang: str = "ru") -> str:
    """
    Генерирует персонализированное сообщение дожима на нужном языке.
    Стадии 1-3: быстрые. Стадии 4+: периодические раз в 3-4 дня.
    """
    lang_msgs = FOLLOWUP_MESSAGES.get(lang, FOLLOWUP_MESSAGES["ru"])
    if stage >= 4:
        options = lang_msgs["periodic"]
    else:
        options = lang_msgs.get(stage, lang_msgs[3])

    template = random.choice(options)
    model_display = _MODEL_NAMES_SHORT.get(interested_model, "")

    # Подставляем плейсхолдеры модели
    if model_display:
        template = template.replace("{model}", f"по {model_display} — ")
        template = template.replace("{model_acc}", f"{model_display} ")
        template = template.replace("{model_dat}", f"{model_display} ")
    else:
        template = template.replace("{model}", "")
        template = template.replace("{model_acc}", "")
        template = template.replace("{model_dat}", "авто ")

    msg = template

    # Персонализируем имя
    if client_name:
        msg = msg.replace("Привет!", f"Привет, {client_name}!")
        msg = msg.replace("Привет,", f"Привет, {client_name},")
        msg = msg.replace("Добрый день!", f"Добрый день, {client_name}!")
        msg = msg.replace("Привет ", f"Привет, {client_name}! ")
        if not msg[0].isupper():
            msg = msg[0].upper() + msg[1:]

    return msg


def get_survey_message(lang: str = "ru", model: str = "") -> str:
    """Сообщение опроса удовлетворённости через неделю после покупки."""
    options = SURVEY_MESSAGES.get(lang, SURVEY_MESSAGES["ru"])
    model_display = _MODEL_NAMES_SHORT.get(model, "автомобиль")
    return random.choice(options).replace("{model}", model_display)


def get_client_mood_label(mood: str) -> str:
    """Возвращает метку настроения для контекста."""
    labels = {
        "happy": "радостное",
        "excited": "воодушевлённое",
        "sad": "грустное",
        "angry": "раздражённое",
        "curious": "любопытное",
        "neutral": "нейтральное",
    }
    return labels.get(mood, "нейтральное")


def analyze_text_mood(text: str) -> str:
    """Простой анализ настроения по тексту."""
    text_lower = text.lower()
    positive_words = ["спасибо", "отлично", "супер", "класс", "круто", "интересно",
                      "хорошо", "нравится", "беру", "хочу", "давайте", "согласен",
                      "rahmat", "yaxshi", "zo'r", "ajoyib"]
    negative_words = ["дорого", "не устраивает", "плохо", "не нравится", "проблема",
                      "жалею", "разочарован", "дорогой", "слишком", "qimmat", "yomon"]
    curious_words = ["сколько", "почём", "цена", "стоимость", "расскажите", "объясните",
                     "как", "что такое", "а если", "necha", "narx", "qancha"]

    if any(w in text_lower for w in positive_words):
        return "happy"
    if any(w in text_lower for w in negative_words):
        return "sad"
    if any(w in text_lower for w in curious_words):
        return "curious"
    if len(text) > 0 and text[-1] == "!":
        return "excited"
    return "neutral"


# ─── Стиль письма и опечатки ───

def analyze_writing_style(text: str) -> float:
    """
    Оценивает «небрежность» письма клиента от 0.0 (аккуратно) до 1.0 (очень небрежно).
    Признаки небрежности: нет пробела после запятой/точки, нет заглавных,
    повторяющиеся знаки (!!!), пропуск знаков препинания в длинных фразах.
    """
    if not text or len(text) < 4:
        return 0.0

    score = 0.0
    t = text.strip()

    # Нет пробела после запятой или точки (кроме чисел)
    import re as _re
    if _re.search(r'[,\.][а-яёa-zА-ЯЁA-Z]', t):
        score += 0.25

    # Повторяющиеся знаки препинания (!!!, ???, )))
    if _re.search(r'[!?)\-]{2,}', t):
        score += 0.2

    # Всё строчными при длине > 15 символов (признак быстрого набора)
    if len(t) > 15 and t == t.lower() and any(c.isalpha() for c in t):
        score += 0.2

    # Слова с очевидными опечатками (удвоение, пропуск гласной) — упрощённо
    words = _re.findall(r'[а-яёa-z]{4,}', t.lower())
    typo_like = sum(1 for w in words if _re.search(r'(.)\1{2,}', w))  # ттт, ааа
    if typo_like:
        score += 0.15

    # Нет знаков препинания в длинном тексте
    if len(t) > 40 and not any(c in t for c in '.,!?'):
        score += 0.1

    return min(score, 1.0)


# Русскоязычные пары соседних клавиш (ЙЦУКЕН) для реалистичных опечаток
_RU_NEIGHBORS: dict[str, str] = {
    'й': 'цу', 'ц': 'йук', 'у': 'цке', 'к': 'уен', 'е': 'кна', 'н': 'егш',
    'г': 'нш', 'ш': 'гщз', 'щ': 'шзх', 'з': 'щхъ',
    'ф': 'ыв', 'ы': 'фва', 'в': 'ыаш', 'а': 'впр', 'п': 'ар', 'р': 'пол',
    'о': 'рлд', 'л': 'одж', 'д': 'лжэ', 'ж': 'дэ',
    'я': 'чс', 'ч': 'яс', 'с': 'чми', 'м': 'си', 'и': 'мт', 'т': 'иь',
    'ь': 'тб', 'б': 'ью', 'ю': 'б',
}
_EN_NEIGHBORS: dict[str, str] = {
    'q': 'wa', 'w': 'qes', 'e': 'wrd', 'r': 'etf', 't': 'ryg',
    'y': 'tuh', 'u': 'yij', 'i': 'uok', 'o': 'ipl', 'p': 'o',
    'a': 'qsz', 's': 'awdx', 'd': 'sefc', 'f': 'drgv', 'g': 'fthb',
    'h': 'gynj', 'j': 'hukm', 'k': 'jil', 'l': 'ko',
    'z': 'ax', 'x': 'zsc', 'c': 'xvd', 'v': 'cbf', 'b': 'vng',
    'n': 'bhmj', 'm': 'njk',
}


def _make_typo(word: str) -> str:
    """Вносит одну реалистичную опечатку в слово."""
    if len(word) < 4:
        return word

    method = random.choices(
        ['swap', 'drop', 'neighbor', 'double'],
        weights=[30, 25, 30, 15],
    )[0]

    chars = list(word)
    # Не трогаем первый и последний символ — опечатки в середине
    pos = random.randint(1, len(chars) - 2)

    if method == 'swap':
        chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]

    elif method == 'drop':
        chars.pop(pos)

    elif method == 'neighbor':
        c = chars[pos].lower()
        neighbors = _RU_NEIGHBORS.get(c) or _EN_NEIGHBORS.get(c)
        if neighbors:
            replacement = random.choice(neighbors)
            if chars[pos].isupper():
                replacement = replacement.upper()
            chars[pos] = replacement
        else:
            chars[pos], chars[pos + 1] = chars[pos + 1], chars[pos]

    elif method == 'double':
        chars.insert(pos, chars[pos])

    result = ''.join(chars)
    return result if result != word else word


def apply_human_typo(text: str, style_score: float) -> tuple[str, str | None]:
    """
    Иногда вносит одну опечатку в текст ответа бота.
    Вероятность зависит от небрежности клиента: 3–12%.
    Возвращает (текст, правильное_слово | None).
    Правильное слово нужно для формирования сообщения-исправления.
    """
    import re as _re

    # Не трогаем короткие сообщения и сообщения с ценами/ссылками
    if len(text) < 30:
        return text, None
    if any(marker in text for marker in ['http', '://', 'сум', '000 000']):
        return text, None

    # Вероятность: минимум 3%, максимум 12% при очень небрежном клиенте
    prob = 0.03 + style_score * 0.09
    if random.random() > prob:
        return text, None

    # Защищённые слова (названия моделей, бренды, имена)
    protected = _re.compile(
        r'\b(Voyah|M-Hero|Taishan|Courage|Freedom|Hero|ОФБ|TAT|AUTO|Жавохир)\b',
        _re.IGNORECASE,
    )
    protected_spans = {(m.start(), m.end()) for m in protected.finditer(text)}

    def is_protected(start: int, end: int) -> bool:
        return any(ps <= start and end <= pe for ps, pe in protected_spans)

    # Ищем целые слова: только строчные буквы, длиннее 4 символов
    candidates = []
    for m in _re.finditer(r'\b[а-яёa-z]{5,}\b', text):
        # Убеждаемся что это самостоятельное слово (не часть слова с заглавной)
        s, e = m.start(), m.end()
        if is_protected(s, e):
            continue
        # Проверяем что перед словом нет буквы (иначе это суффикс заглавного слова)
        if s > 0 and text[s - 1].isalpha():
            continue
        candidates.append((s, m.group()))

    if not candidates:
        return text, None

    start, word = random.choice(candidates)
    typo = _make_typo(word)
    if typo == word:
        return text, None

    new_text = text[:start] + typo + text[start + len(word):]
    return new_text, word  # возвращаем правильное слово для исправления


def make_correction_message(correct_word: str) -> str:
    """Формирует сообщение-исправление как живой человек."""
    options = [
        f"*{correct_word}",
        f"ой, имел в виду «{correct_word}»",
        f"прошу прощения, {correct_word}*",
        f"поправлюсь — {correct_word}",
    ]
    return random.choice(options)


# ─── Детектор языка ───

# Узбекские слова на латинице
_UZ_LATIN_WORDS = {
    "salom", "rahmat", "xayr", "ha", "yoq", "yaxshi", "zo'r", "zo`r",
    "narx", "qancha", "necha", "qachon", "qayer", "qayerda", "qanday",
    "menga", "sizga", "uchun", "bilan", "lekin", "ham", "va", "bu",
    "men", "siz", "u", "biz", "ular", "avto", "mashina", "xarid",
    "ko'rsat", "ko`rsat", "yuborish", "olaman", "berasiz", "kerak",
    "ajoyib", "qimmat", "arzon", "sotib", "olish", "sinab",
}

# Узбекские слова на кириллице
_UZ_CYRILLIC_WORDS = {
    "салом", "раҳмат", "хайр", "яхши", "зўр", "нарх", "қанча",
    "неча", "қачон", "қаер", "қаерда", "қандай", "менга", "сизга",
    "учун", "билан", "лекин", "ҳам", "ва", "бу", "мен", "сиз",
    "биз", "улар", "машина", "харид", "олман", "беринг", "керак",
}

# Кириллические буквы которых нет в русском (характерны для узбекского)
_UZ_SPECIFIC_CYRILLIC = set("ЎўҚқҲҳҒғ")


def detect_language(text: str) -> str:
    """
    Определяет язык текста: 'ru', 'uz', 'en'.
    Возвращает код языка.
    """
    if not text or not text.strip():
        return "ru"

    text_stripped = text.strip()
    words = set(text_stripped.lower().split())

    # Считаем кириллические и латинские символы
    cyrillic = sum(1 for c in text_stripped if "\u0400" <= c <= "\u04ff")
    latin    = sum(1 for c in text_stripped if c.isascii() and c.isalpha())
    total_alpha = cyrillic + latin or 1

    # Если есть узбекские кириллические буквы — точно узбекский
    if any(c in _UZ_SPECIFIC_CYRILLIC for c in text_stripped):
        return "uz"

    # Если текст преимущественно латинский
    if latin / total_alpha > 0.6:
        # Проверяем узбекские слова
        if words & _UZ_LATIN_WORDS:
            return "uz"
        # Узбекские диграфы (sh, ch, ng, oʻ, gʻ)
        t_lower = text_stripped.lower()
        uz_patterns = ["o'", "o`", "g'", "g`", "sh ", " sh", "ch ", " ch", "ng "]
        if sum(1 for p in uz_patterns if p in t_lower) >= 2:
            return "uz"
        return "en"

    # Преимущественно кириллица
    if cyrillic / total_alpha > 0.5:
        if words & _UZ_CYRILLIC_WORDS:
            return "uz"
        return "ru"

    return "ru"


LANG_LABELS = {
    "ru": "русский",
    "uz": "узбекский",
    "en": "английский",
}


# ─── Конкуренты ───

_COMPETITORS: dict[str, dict] = {
    "byd": {
        "name": "BYD",
        "rebuttals": [
            "BYD — массовый бренд, Voyah — это премиум-линейка Dongfeng с совместными технологиями Renault-Nissan. Разные весовые категории.",
            "У BYD нет пневмоподвески и двойного панорамного люка как у Free 318. Voyah — это другой уровень комфорта.",
            "Voyah производится эксклюзивно для нашего рынка — полная поддержка и гарантия от TAT AUTO. У BYD сервис сложнее.",
        ],
    },
    "haval": {
        "name": "Haval",
        "rebuttals": [
            "Haval популярен, но Voyah — платформа Alliance (Renault-Nissan-Mitsubishi). Электрика, подвеска, шумоизоляция — на уровень выше.",
            "Сравните комплектацию: у Voyah Free 318 пневмоподвеска, 318 л.с., масса опций. Haval в этом классе проигрывает по оснащению.",
            "Haval больше рассчитан на выносливость, Voyah — на комфорт и технологии. Зависит от приоритетов.",
        ],
    },
    "chery": {
        "name": "Chery",
        "rebuttals": [
            "Chery — доступный вариант, Voyah — совсем другой класс. Если важен статус и качество интерьера, это разные машины.",
            "Voyah vs Chery — это как сравнивать Lexus и Toyota. Один производитель, но разные сегменты.",
        ],
    },
    "geely": {
        "name": "Geely",
        "rebuttals": [
            "Geely хорошие, но Voyah — эксклюзивно для нашего рынка с прямой поддержкой TAT AUTO. Запчасти, сервис — всё здесь.",
            "У Geely Atlas и Voyah Free+ схожая цена, но оснащение и двигатель у Voyah серьёзно выигрывают.",
        ],
    },
    "toyota": {
        "name": "Toyota",
        "rebuttals": [
            "Toyota — надёжность, Voyah — технологии + надёжность. Наши клиенты, переходящие с Toyota, отмечают, что Voyah выигрывает по комфорту и оснащению.",
            "Сравните цены: за те же деньги что стоит Fortuner — у нас Voyah Free 318 с пневмоподвеской и 318 л.с.",
        ],
    },
    "kia": {
        "name": "Kia",
        "rebuttals": [
            "Kia — хороший выбор, но Voyah в этом ценовом диапазоне предлагает больше: пневмоподвеска, мощный мотор, премиум интерьер.",
        ],
    },
    "hyundai": {
        "name": "Hyundai",
        "rebuttals": [
            "Hyundai надёжен, но Voyah в этом классе даёт больше технологий и комфорта за сопоставимую цену.",
        ],
    },
    "exeed": {
        "name": "Exeed",
        "rebuttals": [
            "Exeed — тоже хороший вариант. Но Voyah — это поддержка TAT AUTO, эксклюзивный дилер, гарантия и сервис прямо здесь в Ташкенте.",
        ],
    },
    "omoda": {
        "name": "Omoda",
        "rebuttals": [
            "Omoda — молодой бренд. Voyah уже зарекомендовал себя, у нас есть клиенты которые ездят больше года — могу дать отзывы.",
        ],
    },
}


def detect_competitor(text: str) -> str | None:
    """Возвращает ключ конкурента если он упомянут в тексте, иначе None."""
    t = text.lower()
    for key, data in _COMPETITORS.items():
        if key in t or data["name"].lower() in t:
            return key
    return None


def get_competitor_rebuttal(competitor_key: str) -> str:
    """Возвращает случайный аргумент против конкурента."""
    data = _COMPETITORS.get(competitor_key, {})
    args = data.get("rebuttals", [])
    if args:
        import random as _r
        return _r.choice(args)
    return ""


# ─── Trade-in ───

_TRADEIN_KEYWORDS = [
    "старая машина", "моя машина", "есть машина", "старый авто", "мой авто",
    "продаю машину", "продам машину", "трейд-ин", "trade-in", "tradein",
    "обменять машину", "в зачёт", "зачёт старого",
    "cobalt", "nexia", "matiz", "lacetti", "spark", "damas", "tico",
    "gentra", "malibu", "captiva", "tracker", "equinox", "tucson",
    "santa fe", "ravon", "daewoo", "chevrolet uz",
    "camry", "corolla", "prado", "rav4", "kia rio", "sportage", "ceed",
    "старый", "б/у машина", "buvchi", "eski mashina", "eski avto",
]


def is_mentioning_tradein(text: str) -> bool:
    """Клиент упоминает свой старый автомобиль / trade-in."""
    t = text.lower()
    return any(kw in t for kw in _TRADEIN_KEYWORDS)


# ─── Горячий лид ───

_HOT_LEAD_SIGNALS = ["asked_price", "asked_credit", "asked_testdrive", "asked_location", "docs_requested", "asked_nasiya"]
HOT_LEAD_THRESHOLD = 3   # сколько сигналов нужно чтобы считать лидом горячим


def get_hot_lead_score(client_info: dict) -> int:
    """Считает количество сигналов готовности к покупке."""
    return sum(1 for s in _HOT_LEAD_SIGNALS if client_info.get(s))


# ─── Детекторы намерений ───

def is_asking_location(text: str) -> bool:
    keywords = [
        "локация", "адрес", "где находитесь", "где вы", "как добраться",
        "location", "address", "where are you", "how to get",
        "manzil", "qayerda", "qayer", "joylashuv", "yoʻl", "yo'l",
        "карта", "map", "xarita", "координаты",
    ]
    return any(kw in text.lower() for kw in keywords)


def is_asking_photo(text: str) -> bool:
    keywords = [
        "фото", "фотка", "фотографии", "покажи", "покажите", "посмотреть", "снимок",
        "photo", "picture", "show", "image", "rasm", "koʻrsat", "ko'rsat", "surat",
    ]
    return any(kw in text.lower() for kw in keywords)


def is_asking_credit(text: str) -> bool:
    keywords = [
        "кредит", "рассрочка", "ежемесячный", "платёж", "взнос", "процент",
        "credit", "installment", "monthly", "kredit", "nasiya", "bo'lib to'lash",
        "лизинг", "мурабаха", "leasing", "murabaha",
    ]
    return any(kw in text.lower() for kw in keywords)


def is_asking_nasiya_leasing(text: str) -> bool:
    """Клиент интересуется насия савдо или лизингом (не кредитом)."""
    keywords = [
        "насия", "nasiya", "лизинг", "leasing", "lizinq", "мурабаха", "murabaha",
        "рассрочка", "bo'lib to'lash", "bo'lib", "nasiyaga",
    ]
    t = text.lower()
    return any(kw in t for kw in keywords)


def is_declining_test_drive(text: str) -> bool:
    """Клиент явно отказывается от тест-драйва."""
    t = text.lower()
    decline_phrases = [
        "не хочу тест", "не нужен тест", "не надо тест", "без тест",
        "не хочу тест-драйв", "не нужен тест-драйв", "не надо тест-драйв",
        "тест не нужен", "тест не надо", "тест-драйв не нужен", "тест-драйв не надо",
        "откажусь от теста", "не буду тест", "пропустить тест",
        "test drive kerak emas", "test kerak emas", "test olmayman",
        "no test", "no test drive", "skip test",
    ]
    return any(ph in t for ph in decline_phrases)


def is_asking_test_drive(text: str) -> bool:
    if is_declining_test_drive(text):
        return False
    keywords = [
        "тест", "тест-драйв", "тестдрайв", "попробовать", "прокатиться", "поездить",
        "test drive", "test", "sinab ko'rish", "sinov",
    ]
    return any(kw in text.lower() for kw in keywords)


def is_asking_price_list(text: str) -> bool:
    """Клиент просит прайс-лист."""
    keywords = [
        "прайс", "прайслист", "прайс-лист", "price list", "pricelist",
        "все цены", "список цен", "каталог", "catalogue", "catalog",
        "цены на все", "все модели цены", "narxlar", "narx list",
        "barcha narx", "прайс лист",
    ]
    return any(kw in text.lower() for kw in keywords)


def is_sticker(event) -> bool:
    """Проверяет является ли сообщение стикером."""
    if not event.message.media:
        return False
    doc = getattr(event.message.media, "document", None)
    if not doc:
        return False
    for attr in doc.attributes:
        attr_type = type(attr).__name__
        if "Sticker" in attr_type:
            return True
    return False


def get_sticker_emoji(event) -> str:
    """Извлекает emoji из стикера (поле alt)."""
    try:
        doc = getattr(event.message.media, "document", None)
        if not doc:
            return ""
        for attr in doc.attributes:
            if type(attr).__name__ == "DocumentAttributeSticker":
                return getattr(attr, "alt", "") or ""
    except Exception:
        pass
    return ""


def get_sticker_reply() -> str:
    """Устаревшая функция — оставлена для совместимости."""
    return get_sticker_text_reply("neutral")


def get_model_from_text(text: str) -> str | None:
    text_lower = text.lower()
    if "courage" in text_lower or "650" in text_lower:
        return "courage"
    elif "free+" in text_lower or "free +" in text_lower or "553" in text_lower:
        return "free_plus"
    elif "free 318" in text_lower or "318" in text_lower or "504" in text_lower:
        return "free_318"
    elif "m817" in text_lower or "m-hero" in text_lower or "hero" in text_lower:
        return "m817"
    elif "taishan" in text_lower:
        return "taishan"
    return None


MODEL_PRICES = {
    "courage": 473_000_000,
    "free_plus": 553_000_000,
    "free_318": 504_000_000,
    "m817": 817_065_000,
    "taishan": 1_060_965_000,
}


CONVERSATION_SUMMARIES_FILE = "conversation_summaries.json"


def load_conversation_summaries() -> dict:
    if os.path.exists(CONVERSATION_SUMMARIES_FILE):
        try:
            with open(CONVERSATION_SUMMARIES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_conversation_summary(chat_id: int, summary: str):
    data = load_conversation_summaries()
    import time as _t
    data[str(chat_id)] = {"summary": summary, "updated_at": _t.time()}
    try:
        with open(CONVERSATION_SUMMARIES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения summary: {e}")


def get_conversation_summary(chat_id: int) -> str:
    data = load_conversation_summaries()
    entry = data.get(str(chat_id), {})
    return entry.get("summary", "")


async def generate_conversation_summary(chat_id: int, messages: list) -> str:
    """Генерирует краткое саммари диалога для долгосрочной памяти AI."""
    if not messages or len(messages) < 4:
        return ""
    # Берём последние 30 сообщений для суммаризации
    recent = messages[-30:]
    conv_text = "\n".join(
        f"{'Клиент' if m['role']=='user' else 'Бот'}: {m['content'][:200]}"
        for m in recent
    )
    prompt = (
        "Сделай краткое резюме этого диалога продажи автомобиля (3-5 предложений). "
        "Укажи: какая модель интересует, какой бюджет, цель покупки, возражения, "
        "что уже обсуждалось. Это будет контекст для следующего разговора с клиентом.\n\n"
        f"{conv_text}"
    )
    try:
        resp = await asyncio.to_thread(
            lambda: client.messages.create(
                model="claude-opus-4-5",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
                timeout=30.0,
            )
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error(f"Ошибка генерации summary: {e}")
        return ""


# ─── Сегментация клиентов ───

def load_customer_status() -> dict:
    if os.path.exists(CUSTOMER_STATUS_FILE):
        try:
            with open(CUSTOMER_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_customer_status(data: dict):
    try:
        with open(CUSTOMER_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения статуса клиента: {e}")


def get_customer_context(chat_id: int, profile: str = "") -> str:
    data = load_customer_status()
    key = str(chat_id)
    if key not in data:
        ctx = "[НОВЫЙ КЛИЕНТ]"
    else:
        status = data[key]
        if status.get("purchased"):
            model = status.get("purchased_model", "автомобиль")
            ctx = f"[ДЕЙСТВУЮЩИЙ КЛИЕНТ - купил: {model}]"
        elif status.get("interested"):
            ctx = f"[ПОТЕНЦИАЛЬНЫЙ КЛИЕНТ - интересовался: {status.get('interested_model', 'не указано')}]"
        else:
            ctx = "[НОВЫЙ КЛИЕНТ]"
    summary = get_conversation_summary(chat_id)
    if summary:
        ctx += f"\n[ИСТОРИЯ ПРЕДЫДУЩИХ РАЗГОВОРОВ: {summary}]"
    if profile:
        ctx += f"\n{profile}"
    return ctx


def mark_customer_purchased(chat_id: int, model: str):
    import time as _time
    data = load_customer_status()
    key = str(chat_id)
    existing = data.get(key, {})
    existing.update({
        "purchased": True,
        "purchased_model": model or existing.get("interested_model", ""),
        "purchased_at": _time.time(),
        "interested": False,
    })
    data[key] = existing
    save_customer_status(data)


def mark_customer_not_interested(chat_id: int):
    """Клиент сказал что не интересуется — останавливаем дожим навсегда."""
    data = load_customer_status()
    key = str(chat_id)
    existing = data.get(key, {})
    existing["not_interested"] = True
    existing["interested"] = False
    data[key] = existing
    save_customer_status(data)


def is_followup_stopped(chat_id: int) -> bool:
    """Возвращает True если дожим нужно остановить (купил или не интересуется)."""
    data = load_customer_status()
    status = data.get(str(chat_id), {})
    return status.get("purchased", False) or status.get("not_interested", False)


def is_ready_to_buy(text: str) -> bool:
    """
    Клиент явно готов купить прямо сейчас.
    Только сильные сигналы — не срабатывает на простой интерес.
    """
    keywords = [
        # Русский
        "беру", "берём", "берем", "оформляем", "оформим", "буду брать",
        "готов купить", "готова купить", "хочу оформить", "давайте оформим",
        "хочу купить", "куплю", "когда можно приехать оформить",
        "можно приехать сегодня", "можно приехать завтра",
        "хочу забрать", "когда забирать", "приедем оформлять",
        "оформите мне", "запишите меня", "готов к покупке",
        "решил купить", "решила купить", "определился", "определилась",
        # Узбекский
        "sotib olaman", "olaman", "rasmiylashtiraman", "rozi bo'ldim",
        "kelaman bugun", "kelaman ertaga", "olishga qaror qildim",
        "sotib olishga tayyorman", "tayyor",
        # Английский
        "i'll buy", "i want to buy", "let's do it", "i'm ready to buy",
        "i'll take it", "ready to purchase", "when can i come",
    ]
    t = text.lower()
    return any(kw in t for kw in keywords)


def get_documents_request_message(lang: str = "ru") -> str:
    """Возвращает сообщение с просьбой предоставить документы."""
    messages = {
        "ru": (
            "Отлично, оформляем! 🎉\n\n"
            "Для составления договора купли-продажи нам понадобятся:\n\n"
            "📄 *Паспорт* — фото разворота с фотографией\n"
            "🏠 *Прописка* — страница с адресом регистрации\n\n"
            "Можете прислать фото документов сюда, или привезти оригиналы в салон:\n"
            "📍 г. Ташкент, ул. Шота Руставели 77"
        ),
        "uz": (
            "Ajoyib, rasmiylashtirамиз! 🎉\n\n"
            "Sotib olish-sotish shartnomasini tuzish uchun kerak bo'ladi:\n\n"
            "📄 *Pasport* — fotosuratli sahifaning rasmi\n"
            "🏠 *Ro'yxatdan o'tish* — yashash manzili sahifasi\n\n"
            "Hujjat rasmini shu yerga yuboring yoki salonimizga olib keling:\n"
            "📍 Toshkent sh., Shota Rustaveli ko'chasi 77"
        ),
        "en": (
            "Great, let's get it done! 🎉\n\n"
            "To prepare the purchase agreement, we'll need:\n\n"
            "📄 *Passport* — photo of the page with your photo\n"
            "🏠 *Proof of residence* — registration address page\n\n"
            "You can send photos here or bring the originals to our showroom:\n"
            "📍 Tashkent, Shota Rustaveli St. 77"
        ),
    }
    return messages.get(lang, messages["ru"])


def is_saying_purchased(text: str) -> bool:
    """Клиент говорит что купил машину."""
    keywords = [
        "купил", "купила", "приобрёл", "приобрела", "приобрел", "взял", "взяла",
        "уже купил", "уже взял", "оформил", "оформила", "забрал", "забрала",
        "машина уже", "авто уже", "уже есть", "уже купили",
        "sotib oldim", "sotib oldi", "oldim", "xarid qildim",
        "bought", "purchased", "already bought", "got the car",
    ]
    t = text.lower()
    return any(kw in t for kw in keywords)


def is_saying_not_interested(text: str) -> bool:
    """Клиент говорит что больше не интересуется."""
    keywords = [
        "не интересует", "не интересуюсь", "не буду брать", "не куплю",
        "раздумал", "раздумала", "отказался", "отказалась", "не нужно",
        "не актуально", "больше не нужно", "не хочу", "откажусь",
        "передумал", "передумала", "не планирую", "не рассматриваю",
        "qiziqmayman", "kerak emas", "olmayman", "not interested",
        "no longer interested", "changed my mind", "not buying",
    ]
    t = text.lower()
    return any(kw in t for kw in keywords)


def mark_customer_interested(chat_id: int, model: str):
    data = load_customer_status()
    key = str(chat_id)
    existing = data.get(key, {})
    if not existing.get("purchased"):
        existing.update({
            "purchased": False,
            "interested": True,
            "interested_model": model,
        })
        data[key] = existing
        save_customer_status(data)


# ─── Расчёт кредита ───

def calculate_credit(price: int, down_pct: float, months: int) -> dict:
    if down_pct >= 50:
        rate_annual, rate_name = 0.229, "22.9%"
    elif down_pct >= 40:
        rate_annual, rate_name = 0.239, "23.9%"
    elif down_pct >= 30:
        rate_annual, rate_name = 0.249, "24.9%"
    else:
        rate_annual, rate_name = 0.255, "25.5%"

    down_amount = int(price * down_pct / 100)
    loan_amount = price - down_amount
    r = rate_annual / 12
    if r == 0:
        monthly = loan_amount / months
    else:
        monthly = loan_amount * r * (1 + r)**months / ((1 + r)**months - 1)

    return {
        "price": price,
        "down_pct": down_pct,
        "down_amount": down_amount,
        "loan_amount": loan_amount,
        "monthly": int(monthly),
        "months": months,
        "rate": rate_name,
        "total": int(monthly * months + down_amount),
    }


_DOWN_RATES = {25: 0.255, 30: 0.249, 40: 0.239, 50: 0.229}


def format_credit_info(price: int, down_pct: float = 30, months: int = 36) -> str:
    """Таблица ежемесячных платежей по разным взносам и срокам."""
    def monthly(loan: int, rate: float, n: int) -> int:
        r = rate / 12
        return int(loan * r / (1 - (1 + r) ** (-n)))

    def fmt(n: int) -> str:
        return f"{n // 1_000_000}млн" if n >= 1_000_000 else f"{n // 1_000}тыс"

    lines = [f"Расчёт кредита — {fmt(price)} сум (Банк ОФБ)\n"]
    for pct, rate in _DOWN_RATES.items():
        down  = int(price * pct / 100)
        loan  = price - down
        parts = [f"Взнос {pct}% ({fmt(down)}):"]
        for m in [24, 36, 48, 60]:
            mp = monthly(loan, rate, m)
            parts.append(f"  {m}мес → {fmt(mp)}/мес")
        lines.append("\n".join(parts))

    lines.append("\nСтавки: 25%→25.5% | 30%→24.9% | 40%→23.9% | 50%→22.9%")
    return "\n\n".join(lines)


# ─── Анализ документов (vision) ───

def analyze_document_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """
    Анализирует фото документа через Claude Vision.
    Возвращает:
      valid      - True если это похоже на нужный документ
      doc_type   - "passport" | "registration" | "other"
      issues     - описание проблем если есть
      summary    - краткое описание что видно
    """
    import base64
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")

    prompt = """Ты проверяешь документы клиента автосалона для оформления договора купли-продажи.

Посмотри на изображение и определи:
1. Что это за документ? (паспорт, страница прописки, другое)
2. Документ читаемый? Текст виден чётко?
3. Это нужный документ или нет?

Ответь строго в формате JSON (без лишнего текста):
{
  "doc_type": "passport" | "registration" | "other",
  "valid": true | false,
  "issues": "описание проблем или пустая строка",
  "summary": "одна строка — что видно на фото"
}

Правила:
- passport: разворот паспорта с фото человека (серия, номер, ФИО, фото)
- registration: страница паспорта с пропиской/адресом регистрации, или отдельная справка
- other: чек, рандомное фото, скриншот и т.п.
- valid=false если: нечитаемо, засвечено, обрезано важное, это не документ"""

    try:
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        import json as _json
        text = response.content[0].text.strip()
        # Вырезаем JSON если он обёрнут в ```
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        result = _json.loads(text)
        return {
            "valid":    bool(result.get("valid", False)),
            "doc_type": result.get("doc_type", "other"),
            "issues":   result.get("issues", ""),
            "summary":  result.get("summary", ""),
        }
    except Exception as e:
        logger.error(f"Ошибка анализа документа: {e}")
        return {"valid": False, "doc_type": "other", "issues": str(e), "summary": ""}


# ─── Определение пола ───

_FEMALE_NAMES = {
    # Узбекские
    "малика","зулайхо","нилуфар","дилноза","гулнора","шахло","мунира","насиба",
    "феруза","барно","дилфуза","мадина","нодира","лола","сабина","камола","зарина",
    "наргиза","умида","мохира","озода","рано","санам","саодат","сайёра","хилола",
    "муштарий","нилуфархон","гулbahor","гулбахор","латофат","мафтуна","шirin",
    "ширин","тамара","хурмо","дилором","моhinur","мохинур","зебо","нозима",
    # Русские / интернациональные
    "анна","елена","мария","ольга","наталья","татьяна","ирина","светлана",
    "юлия","екатерина","алина","дарья","виктория","оксана","марина","людмила",
    "валентина","галина","надежда","кристина","анастасия","александра","вероника",
    "евгения","лариса","нина","полина","софия","яна","жанна","лидия","диана",
    "карина","ксения","милена","валерия","регина","эльвира","альбина","гузель",
}

_MALE_NAMES = {
    # Узбекские
    "жавохир","бобур","фаррух","улугбек","шерзод","отабек","дониёр","санжар",
    "алишер","лазиз","нодирбек","нурбек","аброр","азиз","ахмад","баходир",
    "даврон","жасур","зафар","иброхим","камол","комил","музаффар","мухаммад",
    "ойбек","орзубек","равшан","саид","темур","умар","хасан","холмат","элмурод",
    "шамсиддин","сирожиддин","хурсанд","рустам","бехзод","акбар","анвар",
    "дилшод","илхом","исмоил","муrod","мурод","нозим","ойдин","пулат",
    # Русские / интернациональные
    "александр","дмитрий","сергей","андрей","алексей","михаил","иван","максим",
    "роман","кирилл","николай","владимир","павел","евгений","артём","артем",
    "илья","антон","виктор","константин","тимур","руслан","денис","игорь",
    "юрий","василий","пётр","петр","фёдор","федор","егор","георгий","станислав",
    "вячеслав","леонид","олег","борис","григорий","степан","матвей","арtem",
}


def detect_gender_from_name(name: str) -> tuple:
    """
    Определяет пол по имени.
    Возвращает ('m'|'f'|None, confidence: float).
    """
    if not name:
        return None, 0.0
    first = name.strip().split()[0].lower()
    if first in _FEMALE_NAMES:
        return "f", 0.85
    if first in _MALE_NAMES:
        return "m", 0.85
    # Эвристика по окончанию (узбекские / русские имена)
    if first.endswith(("а", "я", "е")) and len(first) >= 4:
        return "f", 0.45
    if first.endswith(("р", "л", "н", "к", "б", "й")) and len(first) >= 4:
        return "m", 0.40
    return None, 0.0


def detect_gender_from_text(text: str) -> tuple:
    """
    Анализирует грамматику русского текста (форма прошедшего времени).
    Возвращает ('m'|'f'|None, confidence: float).
    """
    t = text.lower()
    # Женский род: глаголы прошедшего на «ла» / «лась»
    fem = _re.findall(r'\b[а-яё]{3,}(?:ла|лась)\b', t)
    # Мужской род: глаголы прошедшего на «л» / «лся» (не «ла», не «лась»)
    masc_all = _re.findall(r'\b[а-яё]{3,}(?:л|лся)\b', t)
    masc = [w for w in masc_all if not w.endswith("ла") and not w.endswith("лась")]

    f_score = min(len(fem)  * 0.2, 0.6)
    m_score = min(len(masc) * 0.2, 0.6)

    if f_score == 0 and m_score == 0:
        return None, 0.0
    if f_score > m_score:
        return "f", round(f_score, 2)
    if m_score > f_score:
        return "m", round(m_score, 2)
    return None, 0.0   # ничья


def analyze_gender_from_photo(image_bytes: bytes) -> tuple:
    """
    Claude Vision: определяет пол по фото профиля.
    Возвращает ('m'|'f'|None, confidence: float).
    """
    import base64 as _b64
    if not image_bytes:
        return None, 0.0
    b64 = _b64.standard_b64encode(image_bytes).decode()
    prompt = (
        "Look at this Telegram profile photo. "
        "Is the main person in the image male or female? "
        "Reply with ONLY one word: male, female, or unknown."
    )
    try:
        resp = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/jpeg", "data": b64,
                }},
                {"type": "text", "text": prompt},
            ]}],
        )
        answer = resp.content[0].text.strip().lower()
        if "female" in answer:
            return "f", 0.75
        if "male" in answer:
            return "m", 0.75
    except Exception as e:
        logger.warning(f"[gender photo] Ошибка Vision: {e}")
    return None, 0.0


# ─── Неизвестные вопросы ───

UNKNOWN_QUESTIONS_FILE = "unknown_questions.json"


def load_unknown_questions() -> list:
    if os.path.exists(UNKNOWN_QUESTIONS_FILE):
        try:
            with open(UNKNOWN_QUESTIONS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_unknown_question(question: str, reply: str):
    questions = load_unknown_questions()
    entry = {
        "question": question,
        "bot_reply": reply,
        "status": "pending",
        "answer": None,
    }
    questions.append(entry)
    try:
        with open(UNKNOWN_QUESTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(questions, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения неизвестного вопроса: {e}")


def is_unknown_answer(reply: str) -> bool:
    reply_lower = reply.lower()
    return any(phrase.lower() in reply_lower for phrase in UNKNOWN_ANSWER_PHRASES)


# ─── API вызов ───

_GENDER_LABEL = {"m": "мужской", "f": "женский"}


def _call_api(
    messages: list,
    customer_context: str,
    mood: str = "neutral",
    followup_stage: int = 0,
    lang: str = "ru",
    gender: str | None = None,
    profile: str = "",
    short_mode: bool = False,
) -> str:
    extra = f"\n\n=== ТЕКУЩИЙ КЛИЕНТ ===\n{customer_context}\n"
    extra += f"[ЯЗЫК: {lang}] ← отвечай ТОЛЬКО на этом языке\n"
    extra += f"[НАСТРОЕНИЕ_КЛИЕНТА: {mood}]\n"
    gender_label = _GENDER_LABEL.get(gender, "неизвестен")
    extra += f"[ПОЛ_КЛИЕНТА: {gender_label}]\n"
    if followup_stage > 0:
        extra += (
            f"[ДОЖИМ_СТАДИЯ:{followup_stage}] ← ты сам пишешь клиенту после нескольких дней молчания.\n"
            f"ОБЯЗАТЕЛЬНО: начни с вежливого приветствия (Здравствуйте / Assalomu alaykum / Hello).\n"
            f"Будь тактичным и ненавязчивым. Задай 1 конкретный вопрос исходя из профиля клиента.\n"
        )
    if short_mode:
        extra += "[РЕЖИМ: краткий ответ, не более 2 предложений]\n"
    system = SYSTEM_PROMPT + extra

    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=1024,
        system=system,
        messages=messages,
        timeout=55.0,
    )
    return response.content[0].text


async def get_ai_reply(
    messages: list,
    chat_id: int = 0,
    client_name: str = "",
    mood: str = "neutral",
    followup_stage: int = 0,
    lang: str = "ru",
    gender: str | None = None,
    profile: str = "",
    short_mode: bool = False,
) -> str | None:
    if not messages:
        return None

    customer_context = get_customer_context(chat_id, profile)
    if client_name:
        customer_context += f"\nИмя клиента: {client_name}"

    # Пробуем до 2 раз, второй раз — короткий режим (fallback)
    for attempt in range(2):
        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(
                    _call_api, messages, customer_context, mood,
                    followup_stage, lang, gender, profile,
                    short_mode or (attempt > 0),
                ),
                timeout=60.0,
            )

            if reply:
                if is_unknown_answer(reply):
                    for msg in reversed(messages):
                        if msg.get("role") == "user":
                            save_unknown_question(msg.get("content", ""), reply)
                            break
                return reply

        except asyncio.TimeoutError:
            logger.warning(f"[AI] Таймаут попытка {attempt+1}/2")
        except anthropic.RateLimitError:
            logger.warning("[AI] Rate limit — ждём 5 сек")
            await asyncio.sleep(5)
        except anthropic.APIConnectionError as e:
            logger.error(f"[AI] Нет соединения: {e}")
            break
        except anthropic.APIStatusError as e:
            logger.error(f"[AI] API ошибка [{e.status_code}]: {e.message}")
            break
        except Exception as e:
            logger.error(f"[AI] Неизвестная ошибка: {e}")
            break

    return None
