# Self-hosted deploy (VPS)

Один `docker compose up -d` на любом Linux-VPS поднимает три сервиса под одним доменом с HTTPS:

| сервис   | роль                                                |
|----------|-----------------------------------------------------|
| `bot`    | aiogram-бот (long-poll, сам ходит в Telegram API)   |
| `backend`| FastAPI для mini-app (`/api/*`, `/healthz`)         |
| `frontend`| статика Vite-сборки на nginx                       |
| `caddy`  | TLS termination + reverse-proxy + Let's Encrypt    |

Бот и backend разделяют один SQLite-файл (`/data/gym_bot.db` в общем volume), поэтому InBody-замеры из бота сразу видны в mini-app и наоборот.

---

## 1. Требования к VPS

- Linux (Ubuntu 22.04/24.04, Debian 12 — проверено)
- 2+ GB RAM (paddleocr тяжёлый; на 1 GB будет OOM)
- 10+ GB свободного диска
- Порты 80 и 443 открыты наружу
- Домен с A-записью на IP VPS (например `mini.example.com`)

## 2. Подготовка VPS

### Установить Docker (если ещё нет)
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# перелогиниться, чтобы не нужен был sudo
```

### Открыть фаервол
```bash
sudo ufw allow 80
sudo ufw allow 443
sudo ufw allow OpenSSH
sudo ufw enable
```

### Склонировать репо
```bash
cd ~
git clone https://github.com/zakazaka3003/gymtgbit.git
cd gymtgbit
```

## 3. Настройка `.env`

```bash
cp .env.example .env
nano .env
```

Заполнить:
```
DOMAIN=mini.example.com    # твой реальный домен
BOT_TOKEN=123456:ABC...    # из @BotFather (свежий, не старый!)
DEV_MODE=0
```

## 4. Запуск

```bash
docker compose up -d --build
```

Первый билд занимает ~5–10 минут (paddlepaddle/torch-зависимости). Дальше `up -d` будет за секунды.

Проверить логи:
```bash
docker compose logs -f bot
docker compose logs -f backend
docker compose logs -f caddy   # тут будет видно как Caddy выпускает TLS
```

Когда увидишь в caddy `certificate obtained successfully` — `https://${DOMAIN}` готов.

## 5. Привязать mini-app к боту

Бот сам берёт `WEBAPP_URL=https://${DOMAIN}` из docker-compose, так что после `compose up` нужно **в Telegram нажать `/start`** — у тебя в Reply-клавиатуре появится кнопка `🚀 Открыть приложение`.

## 6. Менеджмент

```bash
# обновить код и пересобрать (с сохранением /data):
git pull && docker compose up -d --build

# полные логи:
docker compose logs -f

# зайти в backend-контейнер:
docker compose exec backend bash

# бэкап SQLite:
docker compose exec backend cp /data/gym_bot.db /data/gym_bot.$(date +%F).db
docker cp $(docker compose ps -q backend):/data/gym_bot.db ./backup.db
```

## Что если использую Cloudflare?

Если домен за Cloudflare-проксей (оранжевая тучка), Let's Encrypt через HTTP-challenge может ругаться. Два варианта:

1. **Серая тучка (DNS-only)** — самое простое, Caddy выпустит сертификат сам.
2. **Оранжевая тучка** — переключи Cloudflare TLS режим на `Full (strict)`, плюс в Caddyfile добавь
   ```
   tls {
       dns cloudflare YOUR_CF_API_TOKEN
   }
   ```
   и поставь `caddy-dns/cloudflare` плагин. Это не из коробки в стандартном `caddy:2-alpine`, нужно будет собрать кастомный образ.

Самое быстрое для старта — серая тучка.
