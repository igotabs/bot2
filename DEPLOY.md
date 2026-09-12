# Деплой бота на VPS (Ubuntu) як контейнер

VPS: `195.226.192.56` · Репозиторій: `https://github.com/igotabs/bot2.git`

Усі звіти пишуться в локальну папку `./data/reports/` на самому VPS (без SMB-шари, SharePoint чи OneDrive).

## 1. Підключитися до VPS
```bash
ssh root@195.226.192.56
```

## 2. Встановити Docker і git (одноразово)
```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin git
sudo systemctl enable --now docker
```

## 3. Клонувати проєкт
```bash
git clone https://github.com/igotabs/bot2.git
cd bot2
```

## 4. Створити файл із креденшелами
```bash
cp .secrets .secrets.bak 2>/dev/null; nano .secrets
```
Заповнити щонайменше:
- `BOT_TOKEN=` — токен від @BotFather
- `NOTIFY_CHAT_ID=` — свій numeric id (надішліть боту `/myid`)

Зберегти й обмежити доступ:
```bash
chmod 600 .secrets
```

## 5. Запустити
```bash
docker compose --env-file .secrets up -d --build
```

## 6. Перевірити
```bash
docker compose ps
docker compose logs -f          # Ctrl+C щоб вийти з перегляду логів
```
У логах має бути `Bot started (long polling)` без `Conflict`.

Звіти зʼявляться у `./data/reports/reports.xlsx` на VPS.

---

## Оновлення (continuous delivery)
Після зміни коду в репозиторії — на VPS:
```bash
cd bot2
git pull
docker compose --env-file .secrets up -d --build
```
`restart: unless-stopped` сам піднімає бота після перезавантаження VPS.

## Корисне
```bash
docker compose restart        # перезапустити
docker compose down           # зупинити
docker compose logs --tail=100
```
