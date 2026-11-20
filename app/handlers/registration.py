from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.container import Container
from app.database.models import Gender
from app.handlers.menu import show_menu
from app.keyboards.profile import profile_kb
from app.keyboards.registration import (
    confirm_kb, countries_kb, country_results_kb, gender_kb, terms_kb,
)
from app.states.registration import RegStates
from app.utils import countries
from app.utils.legal import PRIVACY_NOTICE
from app.utils.ui import reply
from app.utils.validators import ValidationError, validate_age, validate_name
from app.utils.views import profile_text

router = Router(name="registration")
router.message.filter(F.chat.type == "private")

ORDER = ["name", "age", "gender"]
NOT_COMMAND = ~F.text.startswith("/")


# ── entry points ─────────────────────────────────────────────────────────
async def begin(event: Message | CallbackQuery, state: FSMContext, c: Container) -> None:
    await state.clear()
    await state.set_state(RegStates.terms)
    await state.update_data(mode="register", country=None)
    s = c.settings
    await reply(event, (
        "🌍 <b>Welcome to Global Anonymous Chat!</b>\n\n"
        "Chat with random people from all over the world — anonymously.\n\n"
        f"• You must be at least <b>{s.min_age}</b> years old.\n"
        "• Be respectful. Harassment, spam and illegal content mean a permanent ban.\n"
        "• Partners never see your username, phone number or Telegram ID.\n\n"
        f"{PRIVACY_NOTICE}\n\n"
        "Read the full /terms and /privacy. To continue, confirm below."
    ), terms_kb())


async def ask_field(event: Message | CallbackQuery, state: FSMContext, c: Container, field: str) -> None:
    s = c.settings
    if field == "name":
        await state.set_state(RegStates.name)
        await reply(event, "✍️ What should your partners call you?\n<i>2–30 characters, no links or @usernames.</i>")
    elif field == "age":
        await state.set_state(RegStates.age)
        await reply(event, f"🎂 How old are you?\n<i>Send a number. You must be at least {s.min_age}.</i>")
    elif field == "country":
        await state.set_state(RegStates.country)
        await reply(event, "🌍 Choose your country with the buttons, or <b>type part of its name</b> to search:", countries_kb(0))
    elif field == "gender":
        await state.set_state(RegStates.gender)
        await reply(event, "⚧ What is your gender?", gender_kb())


async def show_confirm(event: Message | CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    profile = {
        "display_name": data["name"], "age": data["age"],
        "country": data.get("country"), "gender": data["gender"],
    }
    await state.set_state(RegStates.confirm)
    await state.update_data(reviewing=False)
    await reply(event, profile_text(profile, "📝 <b>Please confirm your profile</b>"), confirm_kb())


async def after_field(event: Message | CallbackQuery, state: FSMContext, c: Container, field: str) -> None:
    data = await state.get_data()
    uid = event.from_user.id  # type: ignore[union-attr]
    if data.get("mode") == "edit":
        await c.users.update_profile_field(uid, field, data[field])
        await state.clear()
        profile = await c.users.get_profile(uid)
        await reply(event, "✅ Saved.\n\n" + profile_text(profile), profile_kb())  # type: ignore[arg-type]
    elif data.get("reviewing"):
        await show_confirm(event, state)
    else:
        nxt = ORDER.index(field) + 1
        if nxt < len(ORDER):
            await ask_field(event, state, c, ORDER[nxt])
        else:
            await show_confirm(event, state)


# ── step handlers ────────────────────────────────────────────────────────
@router.callback_query(F.data == "reg:agree", StateFilter(RegStates.terms))
async def on_agree(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    await call.answer()
    await container.users.accept_terms(call.from_user.id)
    await ask_field(call, state, container, "name")


@router.message(StateFilter(RegStates.name), F.text, NOT_COMMAND)
async def on_name(message: Message, state: FSMContext, container: Container) -> None:
    try:
        name = validate_name(message.text)
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.update_data(name=name)
    await after_field(message, state, container, "name")


@router.message(StateFilter(RegStates.age), F.text, NOT_COMMAND)
async def on_age(message: Message, state: FSMContext, container: Container) -> None:
    try:
        age = validate_age(message.text, container.settings.min_age, container.settings.max_age)
    except ValidationError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.update_data(age=age)
    await after_field(message, state, container, "age")


@router.callback_query(F.data.startswith("reg:cpage:"), StateFilter(RegStates.country))
async def on_country_page(call: CallbackQuery) -> None:
    await call.answer()
    try:
        page = int(call.data.split(":")[2])
    except (IndexError, ValueError):
        return
    if isinstance(call.message, Message):
        try:
            await call.message.edit_reply_markup(reply_markup=countries_kb(page))
        except Exception:  # noqa: BLE001  (message not modified etc.)
            pass


@router.callback_query(F.data == "reg:noop")
async def on_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.message(StateFilter(RegStates.country), F.text, NOT_COMMAND)
async def on_country_search(message: Message) -> None:
    results = countries.search(message.text or "")
    if not results:
        await message.answer("🔎 No country found. Try another spelling (at least 2 letters) or use the buttons above.")
        return
    await message.answer("Did you mean:", reply_markup=country_results_kb(results))


@router.callback_query(F.data.startswith("reg:country:"), StateFilter(RegStates.country))
async def on_country(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    code = call.data.split(":")[2]
    if not countries.is_valid_code(code):
        await call.answer("Unknown country.", show_alert=True)
        return
    await call.answer()
    await state.update_data(country=code)
    await after_field(call, state, container, "country")


@router.callback_query(F.data.startswith("reg:gender:"), StateFilter(RegStates.gender))
async def on_gender(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    value = call.data.split(":")[2]
    if value not in Gender.ALL:
        await call.answer("Invalid choice.", show_alert=True)
        return
    await call.answer()
    await state.update_data(gender=value)
    await after_field(call, state, container, "gender")


@router.callback_query(F.data.startswith("reg:edit:"), StateFilter(RegStates.confirm))
async def on_edit_from_confirm(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    field = call.data.split(":")[2]
    if field not in ORDER:
        await call.answer()
        return
    await call.answer()
    await state.update_data(reviewing=True)
    await ask_field(call, state, container, field)


@router.callback_query(F.data == "reg:confirm", StateFilter(RegStates.confirm))
async def on_confirm(call: CallbackQuery, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    if not all(k in data for k in ORDER):
        await call.answer("Something is missing, please /start again.", show_alert=True)
        return
    await call.answer()
    await container.users.complete_registration(
        call.from_user.id, data["name"], data["age"], data.get("country"), data["gender"]
    )
    await state.clear()
    await reply(call, "🎉 <b>Registration complete!</b> You can change your profile any time with /profile.")
    await show_menu(call, container)


@router.message(StateFilter(RegStates.terms, RegStates.gender, RegStates.confirm), F.text, NOT_COMMAND)
async def on_wrong_input(message: Message) -> None:
    await message.answer("👆 Please use the buttons above to continue (or /cancel).")
