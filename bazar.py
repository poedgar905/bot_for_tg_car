@dp.message(Form.description)
async def photo_main_step(m: Message, state: FSMContext):
    # Перевіряємо довжину опису
    description_length = len(m.text)
    
    if description_length > MAX_DESCRIPTION_LENGTH:
        await m.answer(
            f"❌ <b>Помилка: Опис занадто довгий!</b>\n\n"
            f"Максимум: {MAX_DESCRIPTION_LENGTH} символів\n"
            f"Ваш опис: {description_length} символів\n\n"
            f"Будь ласка, скороти опис і надішли його ще раз."
        )
        # Залишаємося в тому ж стані
        return
    
    # Продовжуємо якщо все ОК
    await state.update_data(description=m.text, photos=[], media_types=[])
    await m.answer("1️⃣ Надішли ГОЛОВНЕ фото або відео авто...")
    await state.set_state(Form.photo_main)
