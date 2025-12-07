import logging
import re
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
    FSInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage


# --- настройки ---
BOT_TOKEN = "8480610593:AAFCImiLDvdybWeTu1T9sxbSpLbCUlMIqxY"  # 🔒 замени на свой токен
SERVICE_CHAT_ID = -1003244671378   # ID служебного чата
CONTRACT_FILE = "DOGOVOR.pdf"

# --- логирование ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# --- выбор хранилища ---
async def get_storage():
    try:
        storage = RedisStorage.from_url("redis://localhost")
        # пробуем подключиться
        await storage.redis.ping()
        logging.info("✅ Подключено Redis-хранилище FSM")
        return storage
    except Exception as e:
        logging.warning(f"⚠️ Redis недоступен, используется MemoryStorage ({e})")
        return MemoryStorage()


# --- состояния ---
class Form(StatesGroup):
    fio = State()
    iin = State()
    phone = State()
    agreement = State()


# --- основной запуск ---
async def main():
    storage = await get_storage()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=storage)

    # --- старт ---
    @dp.message(Command("start"))
    async def start(message: types.Message, state: FSMContext):
        await state.clear()
        await message.answer("Здравствуйте! Укажите, пожалуйста, ваше ФИО (только буквы):")
        await state.set_state(Form.fio)

    # --- ввод ФИО ---
    @dp.message(Form.fio)
    async def get_fio(message: types.Message, state: FSMContext):
        fio = message.text.strip()
        if not re.match(r"^[А-Яа-яЁёA-Za-z\s\-]+$", fio):
            await message.answer("⚠️ ФИО должно содержать только буквы. Пример: Иванов Иван Иванович")
            return
        await state.update_data(fio=fio)
        await message.answer("Введите, пожалуйста, ваш ИИН (12 цифр):")
        await state.set_state(Form.iin)

    # --- ввод ИИН ---
    @dp.message(Form.iin)
    async def get_iin(message: types.Message, state: FSMContext):
        iin = message.text.strip()
        if not re.match(r"^\d{12}$", iin):
            await message.answer("⚠️ ИИН должен содержать только 12 цифр. Попробуйте снова.")
            return
        await state.update_data(iin=iin)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("Введите ваш номер телефона или нажмите кнопку ниже:", reply_markup=kb)
        await state.set_state(Form.phone)

    # --- обработка контакта ---
    @dp.message(F.contact, Form.phone)
    async def get_contact(message: types.Message, state: FSMContext):
        phone = message.contact.phone_number
        await state.update_data(phone=phone)
        await ask_for_contract(message)

    # --- обработка номера ---
    @dp.message(Form.phone)
    async def get_phone(message: types.Message, state: FSMContext):
        phone = message.text.strip()
        if not re.match(r'^\+?\d{10,15}$', phone):
            await message.answer("⚠️ Введите корректный номер телефона (пример: +79991234567)")
            return
        await state.update_data(phone=phone)
        await ask_for_contract(message)

    # --- запрос договора ---
    async def ask_for_contract(message: types.Message):
        kb = InlineKeyboardBuilder()
        kb.button(text="📄 Прочитать договор", callback_data="read_contract")
        await message.answer("Нажмите, чтобы ознакомиться с договором:", reply_markup=kb.as_markup())

    # --- показать договор ---
    @dp.callback_query(F.data == "read_contract")
    async def send_contract(callback: types.CallbackQuery):
        file = FSInputFile(CONTRACT_FILE)
        await callback.message.answer_document(file, caption="Вот договор. Ознакомьтесь, пожалуйста.")
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Прочитал", callback_data="read_done")
        await callback.message.answer("Прочитали договор?", reply_markup=kb.as_markup())

    # --- подтверждение прочтения ---
    @dp.callback_query(F.data == "read_done")
    async def read_done(callback: types.CallbackQuery):
        kb = InlineKeyboardBuilder()
        kb.button(text="✅ Согласен", callback_data="agree")
        kb.button(text="❌ Не согласен", callback_data="disagree")
        await callback.message.answer("Согласны с условиями договора?", reply_markup=kb.as_markup())

    # --- согласие ---
    @dp.callback_query(F.data == "agree")
    async def agreement_yes(callback: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        logging.info(f"FSM DATA при согласии: {data}")

        fio = data.get("fio")
        iin = data.get("iin")
        phone = data.get("phone")

        if not fio or not iin or not phone:
            await callback.message.answer(
                "⚠️ Не удалось получить ваши данные. Пожалуйста, начните заново командой /start."
            )
            await state.clear()
            return

        await bot.send_message(
            SERVICE_CHAT_ID,
            f"✅ Пользователь согласился с договором.\n\nФИО: {fio}\nИИН: {iin}\nТелефон: {phone}"
        )

        await callback.message.answer(
            "Спасибо! Ваше согласие зафиксировано ✅",
            reply_markup=ReplyKeyboardRemove()
        )
        await state.clear()

    # --- несогласие ---
    @dp.callback_query(F.data == "disagree")
    async def agreement_no(callback: types.CallbackQuery, state: FSMContext):
        kb = InlineKeyboardBuilder()
        kb.button(text="🔁 Начать заново", callback_data="restart")
        await callback.message.answer(
            "Вы отказались от согласия. Процесс завершён ❌",
            reply_markup=kb.as_markup()
        )
        await state.clear()

    # --- начать заново ---
    @dp.callback_query(F.data == "restart")
    async def restart(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.answer("🔁 Процесс начат заново.\n\nУкажите, пожалуйста, ваше ФИО:")
        await state.set_state(Form.fio)

    # --- запуск ---
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
