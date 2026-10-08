from flask import Flask, jsonify, render_template, send_file
from werkzeug.middleware.proxy_fix import ProxyFix
import requests
import pandas as pd
from PIL import Image, ImageFont, ImageDraw
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import contextily as ctx
import geopandas as gpd
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from copernicus_routes import register_copernicus_routes
from copernicus_cems import poll_new_earthquake_activations
import os
import threading
import time
import io
import gc
from collections import deque
from dotenv import load_dotenv
import logging
from pathlib import Path
from xyzservices import TileProvider

load_dotenv()
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
FIREBASE_TOPIC = os.getenv("FIREBASE_TOPIC", "todos").strip() or "todos"
# Coolify Dockerfile pack defaults PORT / Ports Exposes to 3000
PORT = int(os.environ.get("PORT", "3000"))

app = Flask(__name__)
register_copernicus_routes(app)
# Coolify / Traefik terminate TLS and forward X-Forwarded-* headers
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# APIs IPMA
API_CONTINENTE = "https://api.ipma.pt/open-data/observation/seismic/7.json"
API_ACORES = "https://api.ipma.pt/open-data/observation/seismic/3.json"

BOUNDING_BOXES = [
    {
        "name": "Portugal Continental",
        "min_lon": -9.733887,
        "min_lat": 36.791691,
        "max_lon": -6.064453,
        "max_lat": 42.309815,
    },
    {
        "name": "Açores",
        "min_lon": -31.530762,
        "min_lat": 36.544949,
        "max_lon": -24.301758,
        "max_lat": 39.993956,
    },
    {
        "name": "Madeira",
        "min_lon": -18.259277,
        "min_lat": 31.970804,
        "max_lon": -15.380859,
        "max_lat": 33.614619,
    },
    {
        "name": "Ilhas Selvagens",
        "min_lon": -16.163635,
        "min_lat": 29.968022,
        "max_lon": -15.759888,
        "max_lat": 30.210421,
    },
]

# Limitar tamanho do histórico
MAX_SENT = 5000
sismos_enviados = set()
_sismos_order = deque(maxlen=MAX_SENT)  # para limpeza FIFO

# ============================================================
# HTTP / IDENTIFICAÇÃO DA APLICAÇÃO
# ============================================================

APP_VERSION = os.getenv("APP_VERSION", "1.0")

MAP_USER_AGENT = os.getenv(
    "MAP_USER_AGENT",
    f"SismoBot/{APP_VERSION} (+https://teu-dominio.pt)"
)

MAP_REFERER = os.getenv(
    "MAP_REFERER",
    "https://teu-dominio.pt/"
)

session = requests.Session()

session.headers.update({
    "User-Agent": MAP_USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
})

tile_session = requests.Session()

tile_session.headers.update({
    "User-Agent": MAP_USER_AGENT,
    "Referer": MAP_REFERER,
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
})

# ============================================================
# CONFIGURAÇÃO DOS MAPAS
# ============================================================

MAP_PROVIDER = os.getenv("MAP_PROVIDER", "osm").lower()

MAP_ZOOM = int(os.getenv("MAP_ZOOM", "8"))

MAP_TIMEOUT = float(os.getenv("MAP_TIMEOUT", "12"))

# Cache persistente.
TILE_CACHE_DIR = Path(
    os.getenv("TILE_CACHE_DIR", "/tmp/atterratreme-tile-cache")
)

TILE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

OSM_ATTRIBUTION = "© OpenStreetMap contributors"

OSM_PROVIDER = TileProvider(
    name="OpenStreetMap",
    url=OSM_TILE_URL,
    attribution=OSM_ATTRIBUTION,
    min_zoom=0,
    max_zoom=19,
)

PORTUGAL_TZ = ZoneInfo("Europe/Lisbon")

# Lock para evitar geração simultânea de imagens
image_lock = threading.Lock()

def in_bounding_boxes(sismo):
    lat = sismo.get("latitude")
    lon = sismo.get("longitude")

    if lat is None or lon is None:
        return False

    for box in BOUNDING_BOXES:
        if (
            box["min_lat"] <= lat <= box["max_lat"]
            and box["min_lon"] <= lon <= box["max_lon"]
        ):
            return True

    return False

def overlay_text(img, text, position, font, color):
    draw = ImageDraw.Draw(img)
    draw.text(position, text, font=font, fill=color)

def get_map_provider():
    if MAP_PROVIDER == "cartodb":
        return ctx.providers.CartoDB.Voyager

    return OSM_PROVIDER


def create_map_image(df) -> Image.Image:
    """
    Gera o mapa em memória.

    Características:
    - User-Agent identificável
    - Referer
    - cache persistente do contextily
    - zoom controlado
    - timeout
    - attribution
    - fallback sem basemap caso o provider falhe
    """

    latest = df.iloc[-1]

    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(
            df.longitude,
            df.latitude
        ),
        crs="EPSG:4326"
    ).to_crs(epsg=3857)

    latest_point = gdf.iloc[-1]

    cx = latest_point.geometry.x
    cy = latest_point.geometry.y

    # Janela aproximada de 350 km x 350 km.
    window = 175_000

    fig = plt.figure(
        figsize=(6, 6),
        dpi=180
    )

    ax = fig.add_axes([0, 0, 1, 1])

    ax.set_xlim(
        cx - window,
        cx + window
    )

    ax.set_ylim(
        cy - window,
        cy + window
    )

    ax.set_aspect("equal")

    # --------------------------------------------------------
    # BASEMAP
    # --------------------------------------------------------

    basemap_ok = False

    try:
        provider = get_map_provider()

        ctx.add_basemap(
            ax,
            source=provider,
            zoom=MAP_ZOOM,
            headers={
                "User-Agent": MAP_USER_AGENT,
                "Referer": MAP_REFERER,
                "Accept": "image/png,image/*;q=0.9,*/*;q=0.8",
            },
            attribution=OSM_ATTRIBUTION if MAP_PROVIDER == "osm" else None,
            timeout=MAP_TIMEOUT,
            zoom_adjust=0,
        )

        basemap_ok = True

    except Exception as e:
        print(
            f"[MAPA] Basemap indisponível "
            f"(provider={MAP_PROVIDER}): {e}"
        )

    # --------------------------------------------------------
    # FALLBACK
    # --------------------------------------------------------

    if not basemap_ok and MAP_PROVIDER == "osm":

        print(
            "[MAPA] OSM falhou. "
            "A tentar CartoDB Voyager como fallback..."
        )

        try:
            ctx.add_basemap(
                ax,
                source=ctx.providers.CartoDB.Voyager,
                zoom=MAP_ZOOM,
                timeout=MAP_TIMEOUT,
                use_cache=True,
                attribution=None,
            )

            basemap_ok = True

        except Exception as fallback_error:

            print(
                f"[MAPA] Fallback também falhou: "
                f"{fallback_error}"
            )

    # --------------------------------------------------------
    # MARCADOR DO SISMO
    # --------------------------------------------------------

    ax.scatter(
        cx,
        cy,
        s=7000,
        color="red",
        alpha=0.10,
        zorder=2
    )

    ax.scatter(
        cx,
        cy,
        s=2500,
        color="red",
        alpha=0.25,
        zorder=3
    )

    ax.scatter(
        cx,
        cy,
        s=350,
        marker="*",
        color="darkred",
        edgecolors="white",
        linewidth=1.5,
        zorder=4
    )

    ax.text(
        cx,
        cy + 25000,
        f"M {latest['scale']:.1f}",
        fontsize=16,
        fontweight="bold",
        ha="center",
        va="bottom",
        color="black",
        bbox=dict(
            facecolor="white",
            edgecolor="black",
            alpha=0.9,
            boxstyle="round,pad=0.3"
        ),
        zorder=5
    )

    # Se não houve basemap, não deixa o mapa vazio.
    if not basemap_ok:
        ax.text(
            0.5,
            0.03,
            "Mapa base temporariamente indisponível",
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            fontsize=9,
            color="black",
            bbox=dict(
                facecolor="white",
                alpha=0.8,
                edgecolor="none"
            ),
            zorder=10
        )

    ax.set_axis_off()

    fig.subplots_adjust(
        left=0,
        right=1,
        bottom=0,
        top=1
    )

    # --------------------------------------------------------
    # PNG EM MEMÓRIA
    # --------------------------------------------------------

    buf = io.BytesIO()

    fig.savefig(
        buf,
        format="png",
        dpi=180,
        facecolor="white",
        pad_inches=0
    )

    plt.close(fig)
    plt.close("all")

    buf.seek(0)

    img = Image.open(buf).convert("RGB")

    # O PIL pode manter referência ao buffer.
    # Copiamos para memória independente.
    img_copy = img.copy()

    img.close()
    buf.close()

    return img_copy


def generate_final_image(sismo_data) -> bytes:
    """Gera a imagem final completa e devolve os bytes."""
    with image_lock:
        if isinstance(sismo_data, dict):
            df = pd.DataFrame([sismo_data])
        else:
            df = pd.DataFrame(sismo_data)

        map_img = create_map_image(df)

        template = Image.open("assets/SISMO_TEMPLATE_AUTO.png").convert("RGB")
        font = ImageFont.truetype("assets/Lato-Bold.ttf", 38)

        latest = df.iloc[-1]

        overlay_text(template, str(latest['location']).upper(), (390, 559), font, "#703D25")
        overlay_text(template, str(latest['scale']), (455, 629), font, "#703D25")
        overlay_text(template, str(latest['date']), (242, 772), font, "#00A396")
        overlay_text(template, str(latest['intensity']), (520, 832), font, "#00A396")


        # Image with just info (no map)
        info_buf = io.BytesIO()
        template.save("assets/SISMO_INFO.png", optimize=True)
        template.save(info_buf, format="PNG", optimize=True)
        info_buf.seek(0)
        info_data = info_buf.getvalue()
        info_buf.close()

        # Image with the just the map
        map_buf = io.BytesIO()
        map_img.save("assets/MAPA_SISMO.png", optimize=True)
        map_img.save(map_buf, format="PNG", optimize=True)
        map_buf.seek(0)
        map_data = map_buf.getvalue()
        map_buf.close()

        # Image with map (final)
        final = Image.new("RGB", (2160, 1080), color="white")
        final.paste(template, (0, 0))
        final.paste(map_img, (1080, 0))

        final.save("assets/SISMO_TWEET.png", optimize=True)

        # Bytes para envio imediato
        buf = io.BytesIO()
        final.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        data = buf.getvalue()
        buf.close()

        # Limpeza
        del map_img, template, final, df
        gc.collect()

        return data, info_data, map_data


def enviar_discord(sismo, image_bytes: bytes, info_image: bytes, map_image: bytes, tentativas=4):
    if not DISCORD_WEBHOOK:
        print("Webhook não configurado.")
        return False

    mensagem = (
        f"🌍 **Novo sismo registado**\n\n"
        f"📍 Local: {sismo['location']}\n"
        f"📈 Magnitude: {sismo['scale']}\n"
        f"🕒 {sismo['date']}"
    )

    for tentativa in range(1, tentativas + 1):
        try:
            files = {"file1": ("SISMO.png", image_bytes, "image/png"), "file2": ("SISMO_INFO.png", info_image, "image/png"), "file3": ("SISMO_MAP.png", map_image, "image/png")}
            r = session.post(
                DISCORD_WEBHOOK,
                data={"content": mensagem},
                files=files,
                timeout=25
            )

            if r.status_code in (200, 204):
                print(f"Discord: enviado à {tentativa}ª tentativa.")
                return True

            print(f"Discord respondeu {r.status_code} (tentativa {tentativa})")
        except Exception as e:
            print(f"Erro ao enviar para o Discord: {e}")

        time.sleep(3 + tentativa)

    return False


def enviar_firebase(sismo):
    if not FIREBASE_CREDENTIALS_PATH:
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, messaging
    except ImportError:
        print("Firebase configurado, mas firebase-admin não está instalado.")
        return False

    try:
        try:
            firebase_app = firebase_admin.get_app()
        except ValueError:
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
            firebase_app = firebase_admin.initialize_app(cred)

        message = messaging.Message(
            notification=messaging.Notification(
                title="Alerta de Sismo",
                body=(
                    f"Foi detetado um sismo de magnitude {sismo['scale']} "
                    f"em {sismo['location']} às {sismo['date']}."
                ),
            ),
            data={
                "tipo": "sismo",
                "magnitude": str(sismo["scale"]),
                "local": str(sismo["location"]),
                "data": str(sismo["date"]),
                "id": str(sismo.get("id", "")),
            },
            topic=FIREBASE_TOPIC,
            android=messaging.AndroidConfig(
                priority="high",
                notification=messaging.AndroidNotification(
                    sound="default",
                    channel_id="high_importance_channel",
                ),
            ),
        )
        response = messaging.send(message, app=firebase_app)
        print(f"Firebase: notificação enviada ({response}).")
        return True
    except Exception as e:
        print(f"Erro ao enviar notificação Firebase: {e}")
        return False


def obter_sismos():
    sismos = []

    for url, regiao in [(API_CONTINENTE, "Continente e Madeira"), (API_ACORES, "Açores")]:
        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()
            dados = response.json()

            for s in dados.get("data", []):
                mag_str = s.get("magnitud", "-99.0")
                try:
                    mag = float(mag_str)
                    if mag == -99.0 or mag < 0:
                        mag = None
                except (ValueError, TypeError):
                    mag = None

                sismos.append({
                    "areaID": dados.get("idArea"),
                    "obsRegion": s.get("obsRegion") or s.get("regionName"),
                    "magnitude": mag,
                    "depth": s.get("depth"),
                    "intensity": s.get("degree") if s.get("degree") not in (None, "", "0") else "Sem info",
                    "latitude": float(s.get("lat") or s.get("latitude") or 0),
                    "longitude": float(s.get("lon") or s.get("longitude") or 0),
                    "time": s.get("time"),
                    "source": s.get("source", "IPMA"),
                })
        except Exception as e:
            print(f"Erro ao buscar API {regiao}: {e}")

    # Converter time para datetime
    for s in sismos:
        try:
            time_str = s["time"].replace("Z", "")
            s["datetime"] = datetime.fromisoformat(time_str).replace(tzinfo=timezone.utc)
        except Exception:
            try:
                s["datetime"] = datetime.fromisoformat(s["time"]).replace(tzinfo=timezone.utc)
            except Exception:
                try:
                    s["datetime"] = datetime.strptime(
                        s["time"], "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)
                except Exception:
                    s["datetime"] = datetime.now(timezone.utc)

        # Timezone de Potugal Continental
        s["time_pt"] = s["datetime"].astimezone(PORTUGAL_TZ).strftime("%d-%m-%Y pelas %H:%M")

    sismos.sort(key=lambda x: x["datetime"], reverse=True)
    return {
        "owner": "IPMA",
        "country": "PT",
        "total": len(sismos),
        "data": sismos,
    }


def add_enviado(sismo_id: str):
    """Adiciona ao set e remove o mais antigo se ultrapassar o limite."""
    if sismo_id in sismos_enviados:
        return
    if len(sismos_enviados) >= MAX_SENT:
        oldest = _sismos_order.popleft()
        sismos_enviados.discard(oldest)
    sismos_enviados.add(sismo_id)
    _sismos_order.append(sismo_id)

def monitor_sismos():
    print("Monitor de sismos iniciado.")
    consecutive_errors = 0
    cycle_count = 0

    while True:
        cycle_count += 1
        try:
            data = obter_sismos()

            # FILTRO POR BOUNDING BOX
            sismos_monitor = [
                s for s in data["data"]
                if in_bounding_boxes(s)
            ]

            if sismos_monitor:
                novos = [
                    s for s in sismos_monitor
                    if s["time"] not in sismos_enviados
                ]

                # novos = sismos_monitor[:2]  # só para testes

                if novos:
                    novos.sort(key=lambda x: x["datetime"])
                    print(
                        f"Foram encontrados {len(novos)} novos sismos "
                        f"dentro das bounding boxes."
                    )

                    for s in novos:
                        sismo = {
                            "id": s["time"],
                            "location": s.get("obsRegion") or "Portugal",
                            "scale": s["magnitude"] or 0.0,
                            "date": s["time_pt"],
                            "intensity": "Sem info a esta hora",
                            "latitude": s["latitude"],
                            "longitude": s["longitude"],
                        }

                        print(
                            f"Processar → {sismo['location']} "
                            f"M{sismo['scale']} | {sismo['id']}"
                        )

                        firebase_enviado = enviar_firebase(sismo)
                        discord_enviado = False

                        try:
                            image_bytes, info_image, map_image = generate_final_image(sismo)

                            discord_enviado = enviar_discord(
                                sismo,
                                image_bytes,
                                info_image,
                                map_image
                            )

                        except Exception as e:
                            print(
                                f"Erro ao processar sismo {s['time']}: {e}"
                            )

                        if (
                            firebase_enviado
                            or discord_enviado
                            or (not FIREBASE_CREDENTIALS_PATH and not DISCORD_WEBHOOK)
                        ):
                            add_enviado(s["time"])
                            if discord_enviado:
                                time.sleep(1.5)

            consecutive_errors = 0
            gc.collect()

            # CEMS: a cada ~10 ciclos (~7–8 min), independente de haver sismos IPMA
            if cycle_count % 10 == 0:
                try:
                    cems_novos = poll_new_earthquake_activations(
                        session=session,
                        only_relevant_geo=True,
                    )

                    for act in cems_novos:
                        print(
                            f"CEMS nova ativação sísmica: "
                            f"{act.get('code')} — {act.get('name')} "
                            f"({act.get('portal_url')})"
                        )

                        # Enviar aviso para o Discord
                        # if DISCORD_WEBHOOK:
                        #     session.post(
                        #         DISCORD_WEBHOOK,
                        #         json={
                        #             "content": (
                        #                 f"🛰️ **CEMS Rapid Mapping**\n"
                        #                 f"**{act['code']}** — {act['name']}\n"
                        #                 f"{act['portal_url']}"
                        #             )
                        #         },
                        #         timeout=15,
                        #     )

                except Exception as e:
                    print(f"CEMS poll: {e}")

        except Exception as e:
            consecutive_errors += 1
            print(f"Erro no monitor (#{consecutive_errors}): {e}")
            sleep_time = min(30 * consecutive_errors, 180)
            time.sleep(sleep_time)
            continue

        time.sleep(45)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    """Lightweight probe for Docker/Coolify — does not call external APIs."""
    return jsonify({"status": "ok"}), 200


@app.route("/api/sismos")
def api_sismos():
    return jsonify(obter_sismos())


@app.route("/assets/SISMO_TWEET.png")
def download_image():
    path = "assets/SISMO_TWEET.png"
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return "Imagem ainda não gerada.", 404


def bootstrap_monitor():
    """Seed known earthquakes then enter the Discord monitor loop."""
    try:
        data = obter_sismos()
        for s in data["data"]:
            add_enviado(s["time"])
        print(f"{len(sismos_enviados)} sismos existentes ignorados.")
    except Exception as e:
        print(f"Aviso: não foi possível pré-carregar sismos: {e}")

    monitor_sismos()


if __name__ == "__main__":
    os.makedirs("assets", exist_ok=True)

    # Start monitor in background so Flask binds immediately (Coolify healthchecks)
    t = threading.Thread(target=bootstrap_monitor, daemon=True, name="SismoMonitor")
    t.start()

    print(f"A servir em 0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False, threaded=True)