from aiogram.fsm.state import State, StatesGroup


class RegStates(StatesGroup):
    terms = State()
    name = State()
    age = State()
    country = State()
    gender = State()
    confirm = State()
