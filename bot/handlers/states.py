from aiogram.fsm.state import State, StatesGroup


class BetFlow(StatesGroup):
    waiting_stake = State()
    waiting_withdraw = State()
    waiting_deposit_crypto_amount = State()
    waiting_deposit_stars_amount = State()
    waiting_admin_stars_rate = State()
    waiting_admin_bot_token = State()
    waiting_admin_broadcast = State()
    waiting_admin_add_admin = State()
    waiting_admin_grant = State()

