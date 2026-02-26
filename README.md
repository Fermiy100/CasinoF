# Telegram Casino Bot

MVP Telegram-бот казино с house edge, реферальной программой и интеграцией CryptoBot.

## Что реализовано

- Игры: `Кости`, `Футбол`, `Баскетбол`, `Слоты`, `Мины`, `Crash`, `Русская рулетка`
- Команды: `/start`, `/games`, `/bet`, `/balance`, `/profile`, `/deposit`, `/withdraw`, `/ref`, `/support`, `/admin`
- Баланс и история операций в SQLite
- Реферальные выплаты: `10%` от проигрышей приглашённого пользователя
- Пополнение через CryptoBot (инвойсы + фоновая проверка оплаты)
- Пополнение через Telegram Stars (`XTR`) с конвертацией в USD по курсу из `STARS_USD_RATE`
- Вывод через CryptoBot `transfer`
- В `Crash` раунд идет в реальном времени: нужно успеть нажать `Забрать` до взрыва
- В слотах выплата только за `777` (`x10`)
- Админ-панель (`/admin`) для `@fermiy100`: курс Stars, сохранение BOT_TOKEN в `.env`, рассылка, статистика и список балансов

## Технологии

- Python 3.11+
- aiogram 3
- SQLAlchemy async + aiosqlite
- httpx

## Быстрый старт

1. Создайте виртуальное окружение и активируйте его.
2. Установите зависимости:

```bash
pip install -r requirements.txt
```

3. Создайте `.env` на основе примера:

```bash
copy .env.example .env
```

4. Заполните как минимум:

- `BOT_TOKEN`
- `BOT_USERNAME`
- `CRYPTOBOT_API_TOKEN` (если нужны депозиты/выводы)
- `ENABLE_TELEGRAM_STARS=true` (если нужны депозиты в Stars)

5. Запустите бота:

```bash
python -m bot.main
```

## Переменные окружения

См. `.env.example`.

Ключевые настройки house edge:

- `HOUSE_EDGE_SLOTS`
- `HOUSE_EDGE_DICE`
- `HOUSE_EDGE_CRASH`
- `HOUSE_EDGE_ROULETTE`
- `HOUSE_EDGE_MINES`

## Примечания

- БД создаётся автоматически в `casino.db`.
- Если `CRYPTOBOT_API_TOKEN` не задан, пополнение/вывод через CryptoBot будут недоступны.
- Для Stars используется инвойс Telegram в валюте `XTR`.
- Для вывода пользователь отправляет сумму в USD, создается заявка (баланс резервируется сразу), админ подтверждает заявку в админ-панели.
- Команда `/give X` доступна только пользователю `@fermiy100` (начисляет сумму на его баланс).
- При первом `/start` выдается одноразовый приветственный бонус `$0.10`.

## Деплой на Beget

Пошаговый гайд: [DEPLOY_BEGET.md](DEPLOY_BEGET.md)
