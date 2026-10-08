# ATerraTreme

**Real-time seismic monitoring for mainland Portugal, Madeira, and the Azores.**

![Example fo EarthQuake TWEET](assets/SISMO_TWEET.png)

A tool that consumes the official [IPMA](https://www.ipma.pt) APIs, displays earthquakes on an interactive map, and automatically generates alert images ready to publish (Discord / social media).

Used by [VOST Portugal](https://vost.pt) under the **#ATerraTreme** hashtag for public earthquake alerts.

---

## Features

- **Interactive map** (Leaflet) with recent earthquakes, emphasis on the latest ones, and heatmap
- **JSON API** (`/api/sismos`) with unified data for Mainland/Madeira and the Azores
- **Automatic alert image generation** (template + map centered on the epicenter)
- **Discord notifications** via webhook when a new earthquake is detected
- **Firebase Cloud Messaging** topic notifications for subscribed mobile apps (optional)
- **Healthcheck** (`/health`) for orchestrators (Coolify, Docker, etc.)
- **Ready-to-deploy** with Docker / docker-compose

---

## Stack

| Layer           | Technology                                      |
|-----------------|-------------------------------------------------|
| Backend         | Python 3.12 + Flask                             |
| Data            | IPMA Open Data (seismic JSON)                   |
| Maps / images   | GeoPandas, Matplotlib, Contextily, Pillow       |
| Frontend        | Leaflet, Bootstrap Icons, custom CSS            |
| Deploy          | Docker + Coolify-friendly                       |

---

## Requirements

- Python ≥ 3.11 (3.12 recommended)
- System dependencies for GeoPandas/GDAL (already included in the Docker image)
- (Optional) Discord webhook
- (Optional) Firebase service-account JSON for mobile push notifications

---

## Local installation

```bash
git clone https://github.com/vostpt/ATerraTreme.git
cd ATerraTreme

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and set the webhook (optional)
```

Firebase push is optional. Install the `firebase-admin` dependency from
`requirements.txt`, set `FIREBASE_CREDENTIALS_PATH` to the service-account JSON,
and subscribe mobile clients to the `todos` topic (or set `FIREBASE_TOPIC`).
The Android app must also create the `high_importance_channel` notification
channel used by the push payload. Keep the JSON outside version control and
never add it to the Docker image.

Start:

```bash
python app.py
```

By default the application listens on `http://0.0.0.0:3000` (or the value of `PORT`).

---

## Environment variables

| Variable               | Required | Description                                      |
|------------------------|----------|--------------------------------------------------|
| `DISCORD_WEBHOOK_URL`  | No       | Discord webhook URL for earthquake alerts        |
| `FIREBASE_CREDENTIALS_PATH` | No  | Path to a mounted Firebase service-account JSON; unset disables push alerts |
| `FIREBASE_TOPIC` | No | FCM topic to notify (default `todos`) |
| `PORT`                 | No       | HTTP port (default `3000`, Coolify’s default)    |

Example (`.env`):

```env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
FIREBASE_CREDENTIALS_PATH=/run/secrets/firebase-service-account.json
FIREBASE_TOPIC=todos
PORT=3000
```

For Docker/Coolify, mount the credential file as a runtime secret at the path in
`FIREBASE_CREDENTIALS_PATH`; the repository's `notif/` directory is excluded
from the image. Leave the path unset to run normally without Firebase.

---

## Docker

### Build & run

```bash
docker compose up -d --build
```

The application is available on the port defined by `PORT` (exposed internally as 3000).  
For local access, uncomment the `ports` section in `docker-compose.yml`:

```yaml
ports:
  - "9076:3000"
```

### Healthcheck

The container healthcheck probes `/health` on `$PORT` (same port the app listens on).

### Coolify

Use the **Dockerfile** build pack. Coolify injects `PORT=3000` by default — do **not** set `PORT=80` in Coolify env vars (that fights the proxy). Keep only `DISCORD_WEBHOOK_URL` if needed.
---

## Endpoints

| Method | Path                     | Description                                      |
|--------|--------------------------|--------------------------------------------------|
| `GET`  | `/`                      | Web interface (earthquake map)                   |
| `GET`  | `/api/sismos`            | JSON with recent earthquakes (IPMA)              |
| `GET`  | `/assets/SISMO_TWEET.png`| Latest generated alert image                     |
| `GET`  | `/health`                | Healthcheck (status 200 if the app is alive)     |

---

## How the monitor works

1. Every ~45 s the service queries the official IPMA APIs:
   - Mainland + Madeira: `https://api.ipma.pt/open-data/observation/seismic/7.json`
   - Azores: `https://api.ipma.pt/open-data/observation/seismic/3.json`
2. Already processed earthquakes are kept in memory (max. 5000, FIFO).
3. For each new earthquake:
   - Generates a map centered on the epicenter
   - Composes the final image with the alert template
   - Sends the image + message to Discord (if the webhook is configured)
   - Sends an FCM notification to the configured topic (if credentials are configured)

---

## Repository structure

```
ATerraTreme/
├── app.py                 # Flask + earthquake monitor + image generation
├── assets/
│   ├── SISMO_TEMPLATE_AUTO.png
│   ├── Lato-Bold.ttf
│   └── ...
├── static/                # CSS + JS (Leaflet, heatmap)
├── templates/
│   └── index.html
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## License

This project is licensed under the MIT License — see the LICENSE file for details.

---

## Credits

Seismic data: **Instituto Português do Mar e da Atmosfera (IPMA)**  
Original project: [pedrolucas7i/SISMOS](https://github.com/pedrolucas7i/SISMOS)  
Maintained by **[VOST Portugal](https://vost.pt)** ([@VOSTPT](https://x.com/VOSTPT))

#### **Made with ❤️ by Pedro Lucas**