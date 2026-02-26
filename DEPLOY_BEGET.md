# Деплой Telegram-бота на Beget

Ниже рабочий вариант для **Beget Cloud/VPS** (рекомендуется для Telegram-ботов с постоянным polling).

## 1) Подготовка сервера

1. Создай VPS/Cloud сервер в панели Beget (лучше Ubuntu 22.04/24.04).
2. Добавь SSH-ключ в панели.
3. Подключись по SSH:

```bash
ssh root@<SERVER_IP>
```

## 2) Установка окружения

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip git
```

Создай отдельного пользователя под бота:

```bash
adduser casino
usermod -aG sudo casino
su - casino
```

## 3) Загрузка проекта

Вариант A (git):

```bash
git clone <YOUR_REPO_URL> CasinoF
cd CasinoF
```

Вариант B (без git): залей файлы через SFTP/файловый менеджер в `~/CasinoF`.

## 4) Настройка .env

```bash
cp .env.example .env
nano .env
```

Минимум заполни:

- `BOT_TOKEN=...`
- `BOT_USERNAME=...`
- `CRYPTOBOT_API_TOKEN=...` (если нужны крипто-платежи)
- `ADMIN_IDS=...` (через запятую)
- `BETS_CHANNEL=@fermiyyy` (или свой канал)

Проверь баннеры (уже настроены через ссылки):

- `MENU_BANNER=https://t.me/fermiyyy/38`
- `WIN_BANNER=https://t.me/fermiyyy/40`
- `LOSS_BANNER=https://t.me/fermiyyy/39`

## 5) Установка зависимостей

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m compileall bot
```

## 6) Тестовый запуск

```bash
python -m bot.main
```

Если запуск успешный, останови `Ctrl+C` и переходи к автозапуску.

## 7) Автозапуск через systemd

Выйди в root:

```bash
exit
```

Создай unit:

```bash
nano /etc/systemd/system/casinof-bot.service
```

Вставь:

```ini
[Unit]
Description=CasinoF Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=casino
WorkingDirectory=/home/casino/CasinoF
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/casino/CasinoF/.venv/bin/python -m bot.main
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Активируй:

```bash
systemctl daemon-reload
systemctl enable casinof-bot
systemctl start casinof-bot
systemctl status casinof-bot
```

Логи:

```bash
journalctl -u casinof-bot -f
```

## 8) Обновления

```bash
sudo systemctl stop casinof-bot
cd /home/casino/CasinoF
git pull
source .venv/bin/activate
pip install -r requirements.txt
python -m compileall bot
sudo systemctl start casinof-bot
sudo systemctl status casinof-bot
```

## 9) Частые проблемы

### Conflict: terminated by other getUpdates request
Запущено несколько копий бота. Оставь только одну:

```bash
ps aux | grep "python -m bot.main"
pkill -f "python -m bot.main"
sudo systemctl restart casinof-bot
```

### Лок-файл .bot.polling.lock
Если процесс упал некорректно, мог остаться lock-файл:

```bash
rm -f /home/casino/CasinoF/.bot.polling.lock
sudo systemctl restart casinof-bot
```

### Кнопка пополнения/inline падает
Проверь, что у тебя актуальная версия проекта (в ней убран неподдерживаемый `style` у кнопок).

## 10) Важное про Beget shared hosting

Для Telegram-бота с постоянным polling нужен фоновой процесс. Для этого лучше использовать **Beget Cloud/VPS**.
На обычном shared-хостинге долгоживущие процессы обычно ограничены.

## Полезные ссылки (официальные)

- SSH-доступ: https://beget.com/ru/kb/how-to/hosting/kak-vklyuchit-ssh-dostup
- Первые шаги с облачным сервером: https://beget.com/ru/kb/how-to/cloud/pervyj-zapusk-servera
- FAQ по серверам/Cloud: https://beget.com/ru/faq/cloud
