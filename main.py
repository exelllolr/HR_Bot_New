import os
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler
import psycopg2
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from PyPDF2 import PdfReader
from docx import Document
import requests
import io
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import re
import time
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка переменных из .env
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_TOKEN not found in .env!")
    raise ValueError("TELEGRAM_TOKEN not found in .env!")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
GOOGLE_SHEETS_CREDENTIALS = "credentials.json"
DB_CONFIG = {
    "dbname": os.getenv("DB_NAME", "postgres"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "host": os.getenv("DB_HOST", ""),
    "port": os.getenv("DB_PORT", "5432")
}
logger.info(f"Config loaded: TELEGRAM_TOKEN={TELEGRAM_TOKEN[:5]}..., DB_HOST={DB_CONFIG['host']}")

# Состояния для ConversationHandler
VACANCY, RESUME, PROCESSING = range(3)

app = FastAPI()

# Инициализация бота
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Подключение к Google Sheets
def get_sheets_service():
    creds = Credentials.from_authorized_user_file(GOOGLE_SHEETS_CREDENTIALS)
    service = build("sheets", "v4", credentials=creds)
    return service

# Подключение к PostgreSQL
def get_db_connection():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        logger.info("✅ Database connection established!")
        return conn
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        raise

# Проверка авторизации
def is_authorized_user(user_id):
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

# Обработка команды /start
async def start(update: Update, context):
    user_id = update.effective_user.id
    logger.info(f"Received /start from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Ой-ой! Доступ запрещён! Обратитесь к администратору для добавления вас в команду! 📞")
        return
    await update.message.reply_text(
        "🌟 Привет, герой подбора! Я твой AI-рекрутер! Готов помочь найти звезду для твоей команды! Нажми /add_vacancy, чтобы начать! ✨"
    )

# Добавление пользователя
async def add_user(update: Update, context):
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

# Добавление вакансии
async def add_vacancy(update: Update, context):
    user_id = update.effective_user.id
    logger.info(f"Received /add_vacancy from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Доступ только для HR и работодателей! 😄")
        return ConversationHandler.END
    await update.message.reply_text(
        "🌟 Введи вакансию в формате: Должность, Требования, Зарплата (например, 'Программист, Python, 120к')! ✨"
    )
    return VACANCY

# Сохранение вакансии
async def save_vacancy(update: Update, context):
    user_id = update.effective_user.id
    logger.info(f"Saving vacancy for user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Доступ запрещён! 😞")
        return ConversationHandler.END
    vacancy_data = update.message.text
    parts = vacancy_data.split(",", 2)
    if len(parts) >= 2:
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

# Обработка резюме
async def handle_resume(update: Update, context):
    user_id = update.effective_user.id
    logger.info(f"Handling resume for user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Только для своих! 😄")
        return ConversationHandler.END
    file = await update.message.document.get_file()
    file_path = await file.download_to_drive()
    text = extract_text(file_path)
    vacancy_id = context.user_data["vacancy_id"]
    
    score, analysis = analyze_resume(text, context.user_data.get("vacancy_data"))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO resumes (vacancy_id, user_id, resume_text, score, analysis) VALUES (%s, %s, %s, %s, %s)",
        (vacancy_id, user_id, text, score, analysis)
    )
    conn.commit()
    cursor.close()
    conn.close()
    
    append_to_sheets(vacancy_id, text, score, analysis)
    
    pdf_path = generate_pdf_report(vacancy_id, text, score, analysis)
    with open(pdf_path, "rb") as f:
        await update.message.reply_document(document=f, caption="📋 Вот твой отчёт! 🌟")
    
    await update.message.reply_text("🎉 Резюме обработано! Хочешь загрузить ещё? (/add_resume) Или завершить? (/finish) 🚀")
    return PROCESSING

# Извлечение текста
def extract_text(file_path):
    if file_path.endswith(".pdf"):
        with open(file_path, "rb") as f:
            pdf = PdfReader(f)
            return " ".join(page.extract_text() or "" for page in pdf.pages)
    elif file_path.endswith(".docx"):
        doc = Document(file_path)
        return " ".join(paragraph.text or "" for paragraph in doc.paragraphs)
    return ""

# Анализ резюме
def analyze_resume(resume_text, vacancy_data):
    prompt = (
        f"Анализируй резюме: {resume_text}\n"
        f"Вакансия: {vacancy_data}\n"
        f"Оцени по шкале от 0 до 10 с одним десятичным знаком (например, 7.5) и дай краткий анализ (2-3 предложения) с позитивным настроением! 😄"
    )
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    time.sleep(1)
    response = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers=headers,
        json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}], "stream": False}
    )
    result = response.json()["choices"][0]["message"]["content"]
    score = extract_score(result)
    return score, result

# Извлечение оценки
def extract_score(gpt_response):
    match = re.search(r"\b\d+\.\d\b", gpt_response)
    return float(match.group()) if match else 5.0

# Добавление в Google Sheets
def append_to_sheets(vacancy_id, resume_text, score, analysis):
    service = get_sheets_service()
    sheet = service.spreadsheets()
    values = [[vacancy_id, resume_text[:100], score, analysis[:100]]]
    sheet.values().append(
        spreadsheetId="YOUR_SPREADSHEET_ID",
        range="Sheet1!A:D",
        valueInputOption="RAW",
        body={"values": values}
    ).execute()

# Генерация PDF-отчёта
def generate_pdf_report(vacancy_id, resume_text, score, analysis):
    output_path = f"report_{vacancy_id}.pdf"
    c = canvas.Canvas(output_path, pagesize=letter)
    c.drawString(100, 750, f"Отчёт по вакансии #{vacancy_id} 🌟")
    c.drawString(100, 730, f"Оценка: {score:.1f} ⭐")
    c.drawString(100, 710, "Анализ:")
    c.drawString(100, 690, analysis[:500])
    c.save()
    return output_path

# Завершение
async def finish(update: Update, context):
    user_id = update.effective_user.id
    logger.info(f"Received /finish from user {user_id}")
    if not is_authorized_user(user_id):
        await update.message.reply_text("⛔ Доступ только для своих! 😄")
        return ConversationHandler.END
    await update.message.reply_text("🎉 Поиск завершён! Вот топ-3 кандидатов, готовых сиять в твоей команде! 🌟")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT resume_text, score, analysis FROM resumes WHERE vacancy_id = %s ORDER BY score DESC LIMIT 3",
        (context.user_data["vacancy_id"],)
    )
    shortlist = cursor.fetchall()
    cursor.close()
    conn.close()
    
    for i, (resume, score, analysis) in enumerate(shortlist, 1):
        await update.message.reply_text(f"🏆 Кандидат {i}:\nОценка: {score:.1f} ⭐\nАнализ: {analysis[:100]}")
    
    return ConversationHandler.END

# Просмотр данных для админов
async def admin_view(update: Update, context):
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
        await update.message.reply_text(f"🌟 Вакансия #{resume[0]}: Оценка {resume[2]}, Анализ: {resume[3][:100]}")

# Регистрация обработчиков
def setup_handlers():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add_user", add_user))
    application.add_handler(CommandHandler("add_vacancy", add_vacancy))
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
    application.add_handler(CommandHandler("admin_view", admin_view))

# Вебхук для обработки обновлений
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"status": "ok"}

# Инициализация при старте
setup_handlers()