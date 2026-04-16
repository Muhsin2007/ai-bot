import os
import logging
import subprocess
import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

api_key = os.getenv("ANTHROPIC_API_KEY")
_anthropic_client = anthropic.Anthropic(api_key=api_key)


def convert_to_wav(input_path: str, output_path: str) -> bool:
    """Конвертирует аудио в WAV через ffmpeg."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", output_path],
            capture_output=True,
            timeout=30
        )
        return result.returncode == 0
    except FileNotFoundError:
        logger.error("ffmpeg не найден. Установите: sudo apt install ffmpeg")
        return False
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timeout при конвертации")
        return False
    except Exception as e:
        logger.error(f"Ошибка конвертации аудио: {e}")
        return False


def transcribe_voice(voice_path: str) -> str | None:
    """
    Транскрибирует голосовое сообщение.
    Использует OpenAI Whisper API через whisper.cpp или openai SDK.
    Если OPENAI_API_KEY задан — использует OpenAI Whisper.
    Иначе — локальный whisper через subprocess.
    """
    openai_key = os.getenv("OPENAI_API_KEY")

    # Вариант 1: OpenAI Whisper API (рекомендуется)
    if openai_key:
        return _transcribe_openai(voice_path, openai_key)

    # Вариант 2: Локальный faster-whisper
    return _transcribe_local(voice_path)


def _transcribe_openai(voice_path: str, api_key: str) -> str | None:
    """Транскрипция через OpenAI Whisper API."""
    try:
        import openai
        oa_client = openai.OpenAI(api_key=api_key)

        # Whisper принимает ogg/mp3/wav/mp4 напрямую
        with open(voice_path, "rb") as audio_file:
            transcript = oa_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                language=None,  # авто-определение языка
            )

        text = transcript.strip() if isinstance(transcript, str) else transcript.text.strip()
        if not text:
            logger.warning("Whisper API вернул пустой текст")
            return None
        return text

    except ImportError:
        logger.error("openai не установлен. Запустите: pip install openai")
        return None
    except Exception as e:
        logger.error(f"Ошибка OpenAI Whisper API: {e}")
        return None


def _transcribe_local(voice_path: str) -> str | None:
    """
    Транскрипция через faster-whisper (локально, без интернета).
    Установка: pip install faster-whisper
    """
    try:
        from faster_whisper import WhisperModel

        model_size = os.getenv("WHISPER_MODEL", "small")  # tiny / base / small / medium
        model = WhisperModel(model_size, device="cpu", compute_type="int8")

        segments, info = model.transcribe(voice_path, beam_size=5)
        text = " ".join(segment.text.strip() for segment in segments).strip()

        if not text:
            logger.warning("faster-whisper вернул пустой текст")
            return None

        logger.info(f"Распознан язык: {info.language} (уверенность {info.language_probability:.2f})")
        return text

    except ImportError:
        logger.error("faster-whisper не установлен. Запустите: pip install faster-whisper")
        return _transcribe_stub()
    except Exception as e:
        logger.error(f"Ошибка faster-whisper: {e}")
        return None


def _transcribe_stub() -> str | None:
    """Заглушка если ни один метод недоступен."""
    logger.warning("Транскрипция недоступна. Установите openai или faster-whisper.")
    return None


def synthesize_voice(text: str, output_path: str) -> bool:
    """
    Синтез речи из текста.
    Если OPENAI_API_KEY задан — использует OpenAI TTS.
    """
    openai_key = os.getenv("OPENAI_API_KEY")

    if openai_key:
        return _synthesize_openai(text, output_path, openai_key)

    logger.warning("OPENAI_API_KEY не задан — синтез речи недоступен")
    return False


def _synthesize_openai(text: str, output_path: str, api_key: str) -> bool:
    """Синтез речи через OpenAI TTS API."""
    try:
        import openai
        oa_client = openai.OpenAI(api_key=api_key)

        response = oa_client.audio.speech.create(
            model="tts-1",
            voice="alloy",   # alloy / echo / fable / onyx / nova / shimmer
            input=text[:4096],  # TTS ограничен 4096 символами
        )

        response.stream_to_file(output_path)
        return True

    except ImportError:
        logger.error("openai не установлен. Запустите: pip install openai")
        return False
    except Exception as e:
        logger.error(f"Ошибка OpenAI TTS: {e}")
        return False
