from aiogram.fsm.state import State, StatesGroup


class ReportStates(StatesGroup):
    context = State()
