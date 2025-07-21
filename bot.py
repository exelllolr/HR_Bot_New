import os
from PyPDF2 import PdfReader
from docx import Document
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
import psycopg2
import requests
import re
import time
import logging
from tempfile import NamedTemporaryFile

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN not found in .env!")
    raise ValueError("TELEGRAM_TOKEN not found in .env!")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "host": os.getenv("DB_HOST", ""),
    "port": os.getenv("DB_PORT", "5432")
}

# Глобальная переменная для Telegram Application
_application = None

# Состояния для ConversationHandler
VACANCY, RESUME, PROCESSING = range(3)

def get_application():
    """Ленивая инициализация Telegram Application."""
    global _application
    if _application is None:
        logger.info("Initializing Telegram Application")
        _application = Application.builder().token(TELEGRAM_TOKEN).build()
        setup_handlers(_application)
    return _application

def get_db_connection():
    """Подключение к PostgreSQL."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("✅ Database connection established!")
        return conn
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise

def is_authorized_user(user_id):
    """Проверка авторизации пользователя."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE telegram_id = %s", (user_id,))
        result = cursor.fetchone()
        logger.debug(f"Checking user {user_id}, result: {result}")
        cursor.close()
        conn.close()
        return result and result[0] in ["HR", "Employer", "Admin"]
    except Exception as e:
        logger.error(f"❌ Authorization check failed: {e}")
        return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка команды /start."""
    user_id = update.effective_user.id
    logger.info(f"Received /start from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Ой-ой! Доступ запрещён! Обратитесь к администратору для добавления вас в команду! 📞")
        return
    await update.message.reply_text(
        "🌟 Привет, герой подбора! Я твой AI-рекрутер! Готов помочь найти звезду для твоей команды! Нажми /add_vacancy, чтобы начать! ✨"
    )

async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление нового пользователя."""
    user_id = update.effective_user.id
    logger.info(f"Received /add_user from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Только админ может добавлять новых героев! 😄")
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE telegram_id = %s", (user_id,))
    role = cursor.fetchone()
    if role and role[0] != 'Admin':
        await update.message.reply_text("⛔ Только админ может добавлять пользователей! 👮‍♂️")
        cursor.close()
        conn.close()
        return
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("⚠️ Используй: /add_user <telegram_id> <role> (HR, Employer, Admin)! 📝")
        cursor.close()
        conn.close()
        return
    telegram_id, role = int(args[0]), args[1].capitalize()
    if role not in ["HR", "Employer", "Admin"]:
        await update.message.reply_text("⚠️ Роль должна быть HR, Employer или Admin! 😄")
        cursor.close()
        conn.close()
        return
    cursor.execute("INSERT INTO users (telegram_id, role) VALUES (%s, %s) ON CONFLICT DO NOTHING", (telegram_id, role))
    conn.commit()
    cursor.close()
    conn.close()
    await update.message.reply_text(f"🎉 Новый герой {telegram_id} с ролью {role} добавлен в команду! 🚀")

async def add_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Добавление вакансии."""
    user_id = update.effective_user.id
    logger.info(f"Received /add_vacancy from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Доступ только для HR и работодателей! 😄")
        return ConversationHandler.END
    await update.message.reply_text(
        "🌟 Введи вакансию в формате: Должность, Требования, Зарплата (например, 'Программист, Python, 120к')! ✨"
    )
    return VACANCY

async def save_vacancy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сохранение вакансии."""
    user_id = update.effective_user.id
    logger.info(f"Saving vacancy for user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Доступ запрещён! 😞")
        return ConversationHandler.END
    vacancy_data = update.message.text
    parts = vacancy_data.split(",", 2)
    if len(parts) < 2:
        await update.message.reply_text("⚠️ Введи вакансию в формате: Должность, Требования, Зарплата! 😄")
        return VACANCY
    position = parts[0].strip()
    requirements = parts[1].strip()
    salary = parts[2].strip() if len(parts) > 2 else "Не указана"
    vacancy_data = f"Должность: {position}, Требования: {requirements}, Зарплата: {salary}"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO vacancies (user_id, vacancy_data) VALUES (%s, %s) RETURNING id",
        (user_id, vacancy_data)
    )
    vacancy_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()
    context.user_data["vacancy_id"] = vacancy_id
    context.user_data["vacancy_data"] = vacancy_data
    await update.message.reply_text(f"🎉 Вакансия сохранена! Загрузи резюме (PDF/DOCX) и давай найдём звезду! 🌟")
    return RESUME

async def handle_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка загруженного резюме."""
    user_id = update.effective_user.id
    logger.info(f"Handling resume for user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Только для своих! 😄")
        return ConversationHandler.END
    if not update.message.document:
        await update.message.reply_text("⚠️ Пожалуйста, загрузи файл PDF или DOCX! 😄")
        return RESUME
    file = await update.message.document.get_file()
    with NamedTemporaryFile(delete=False, suffix=".pdf" if update.message.document.mime_type == "application/pdf" else ".docx") as tmp_file:
        await file.download_to_drive(tmp_file.name)
        text = extract_text(tmp_file.name)
    if not text:
        await update.message.reply_text("⚠️ Не удалось извлечь текст из файла! Попробуй другой файл! 😄")
        return RESUME
    vacancy_id = context.user_data.get("vacancy_id")
    if not vacancy_id:
        await update.message.reply_text("⚠️ Вакансия не найдена! Начни заново с /add_vacancy! 😄")
        return ConversationHandler.END
    score, analysis = analyze_resume(text, context.user_data.get("vacancy_data", ""))
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO resumes (vacancy_id, user_id, resume_text, score, analysis) VALUES (%s, %s, %s, %s, %s)",
        (vacancy_id, user_id, text, score, analysis)
    )
    conn.commit()
    cursor.close()
    conn.close()
    await update.message.reply_text(
        f"🎉 Резюме обработано! Оценка: {score:.1f}, Анализ: {analysis[:100]}...\n"
        "Хочешь загрузить ещё? (/add_resume) Или завершить? (/finish) 🚀"
    )
    return PROCESSING

def extract_text(file_path):
    """Извлечение текста из PDF или DOCX."""
    try:
        if file_path.endswith(".pdf"):
            with open(file_path, "rb") as f:
                pdf = PdfReader(f)
                return " ".join(page.extract_text() or "" for page in pdf.pages)
        elif file_path.endswith(".docx"):
            doc = Document(file_path)
            return " ".join(paragraph.text or "" for paragraph in doc.paragraphs)
        return ""
    except Exception as e:
        logger.error(f"Error extracting text: {e}")
        return ""
    finally:
        try:
            os.unlink(file_path)
        except Exception as e:
            logger.error(f"Error deleting temp file: {e}")

def analyze_resume(resume_text, vacancy_data):
    """Анализ резюме с помощью DeepSeek API."""
    if not DEEPSEEK_API_KEY:
        logger.error("DEEPSEEK_API_KEY not found!")
        return 5.0, "Ошибка: API-ключ DeepSeek не настроен."
    prompt = (
        f"Анализируй резюме: {resume_text[:2000]}\n"
        f"Вакансия: {vacancy_data}\n"
        f"Оцени по шкале от 0 до 10 с одним десятичным знаком (например, 7.5) и дай краткий анализ (2-3 предложения) с позитивным настроением! 😄"
    )
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    try:
        time.sleep(1)  # Ограничение скорости для API
        response = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers=headers,
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "stream": False}
        )
        response.raise_for_status()
        result = response.json()["choices"][0]["message"]["content"]
        score = extract_score(result)
        return score, result
    except Exception as e:
        logger.error(f"DeepSeek API error: {e}")
        return 5.0, f"Ошибка анализа: {str(e)}. Попробуем ещё раз? 😄"

def extract_score(gpt_response):
    """Извлечение оценки из ответа DeepSeek."""
    match = re.search(r"\b\d+\.\d\b", gpt_response)
    return float(match.group()) if match else 5.0

async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершение обработки вакансии и вывод shortlist."""
    user_id = update.effective_user.id
    logger.info(f"Received /finish from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Доступ только для своих! 😄")
        return ConversationHandler.END
    vacancy_id = context.user_data.get("vacancy_id")
    if not vacancy_id:
        await update.message.reply_text("⚠️ Вакансия не найдена! Начни заново с /add_vacancy! 😄")
        return ConversationHandler.END
    await update.message.reply_text("🎉 Поиск завершён! Вот топ-3 кандидатов, готовых сиять в твоей команде! 🌟")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT resume_text, score, analysis FROM resumes WHERE vacancy_id = %s ORDER BY score DESC LIMIT 3",
        (vacancy_id,)
    )
    shortlist = cursor.fetchall()
    cursor.close()
    conn.close()
    if not shortlist:
        await update.message.reply_text("📭 Пока нет резюме для этой вакансии! Загрузи ещё! 😄")
        return ConversationHandler.END
    for i, (resume, score, analysis) in enumerate(shortlist, 1):
        await update.message.reply_text(f"🏆 Кандидат {i}:\nОценка: {score:.1f} ⭐\nАнализ: {analysis[:100]}...")
    context.user_data.clear()
    return ConversationHandler.END

async def admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр всех резюме для админов."""
    user_id = update.effective_user.id
    logger.info(f"Received /admin_view from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Только админ может заглянуть за кулисы! 😄")
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE telegram_id = %s", (user_id,))
    role = cursor.fetchone()
    if role and role[0] != 'Admin':
        await update.message.reply_text("⛔ Только админ имеет доступ! 👮‍♂️")
        cursor.close()
        conn.close()
        return
    cursor.execute("SELECT vacancy_id, resume_text, score, analysis FROM resumes")
    resumes = cursor.fetchall()
    cursor.close()
    conn.close()
    if not resumes:
        await update.message.reply_text("📭 Пока нет резюме для просмотра! Добавь вакансии и резюме! 🌟")
        return
    for resume in resumes:
        await update.message.reply_text(f"🌟 Вакансия #{resume[0]}: Оценка {resume[2]:.1f}, Анализ: {resume[3][:100]}...")

def setup_handlers(application: Application):
    """Регистрация обработчиков команд и ConversationHandler."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_user", add_user))
    application.add_handler(CommandHandler("admin_view", admin_view))
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_vacancy", add_vacancy)],
        states={
            VACANCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_vacancy)],
            RESUME: [MessageHandler(filters.Document.ALL, handle_resume)],
            PROCESSING: [
                CommandHandler("add_resume", add_vacancy),
                CommandHandler("finish", finish)
            ]
        },
        fallbacks=[]
    )
    application.add_handler(conv_handler)