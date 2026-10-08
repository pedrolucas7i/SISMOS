//===================================================
// MAPA E DADOS
//===================================================

const loadingEl = document.getElementById("loading");
const totalEl = document.getElementById("total-sismos");

const map = L.map("map", {
    center: [39.6945, -8.1306],
    zoom: 5,
    minZoom: 2,
    maxZoom: 13,
    zoomControl: false,
    tap: true
});

L.control.zoom({
    position: "bottomright"
}).addTo(map);

const basemaps = {
    dark: L.tileLayer(
        "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        {
            attribution: "© OpenStreetMap © CARTO"
        }
    ),
    osm: L.tileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            attribution: "© OpenStreetMap"
        }
    )
};

basemaps.dark.addTo(map);

const markersLayer = L.layerGroup().addTo(map);
const heatLayer = L.heatLayer([], {
    radius: 50,
    blur: 25,
    maxZoom: 13,
    minOpacity: 0.35,
    max: 1,
    gradient: {
        0.00: "transparent",
        0.08: "#FFF3C4",
        0.18: "#FFE082",
        0.30: "#FFD54F",
        0.42: "#FFB300",
        0.54: "#FB8C00",
        0.66: "#F57C00",
        0.78: "#EF6C00",
        0.88: "#F4511E",
        0.94: "#E53935",
        0.98: "#C62828",
        1.00: "#7F0000"
    }
});

// Camada das fronteiras das placas tectónicas
const platesLayer = L.geoJSON(null, {
    style: {
        color: "#ff7043",
        weight: 1.5,
        opacity: 0.7,
        lineCap: "round",
        lineJoin: "round"
    },
    interactive: false,
    attribution: 'Placas: <a href="https://github.com/fraxen/tectonicplates">PB2002</a> (ODC-By 1.0)'
});

// Zoom máximo em que as placas ainda são mostradas
const PLATES_MAX_ZOOM = 7;

const state = {
    markers: [],
    earthquakes: [],
    heatVisible: false,
    darkMode: true,
    refreshTimer: null,
    hasLoaded: false,
    firstLoad: true
};

function atualizarVisibilidadePlacas() {
    const zoom = map.getZoom();

    if (zoom <= PLATES_MAX_ZOOM) {
        if (!map.hasLayer(platesLayer)) {
            platesLayer.addTo(map);
        }
    } else {
        if (map.hasLayer(platesLayer)) {
            map.removeLayer(platesLayer);
        }
    }
}

function corMagnitude(m) {
    if (m == null) return "#555";
    if (m < 2) return "#4a4a4a";
    if (m < 3) return "#7B4B1A";
    if (m < 4) return "#A66A2B";
    if (m < 5) return "#D46A00";
    if (m < 6) return "#E53935";
    return "#8E0000";
}

function raioMagnitude(m) {
    if (m == null) return 5;
    return 4 + Math.pow(m, 1.5);
}

/** Verifica se o sismo foi sentido (tem intensidade válida) */
function temIntensidade(s) {
    if (s.intensity == null || s.intensity == "Sem info") return false;
    const val = String(s.intensity).trim().toLowerCase();
    return val !== "" && val !== "—" && val !== "-" && val !== "n/a" && val !== "null" && val !== "0";
}

function criarPopup(s) {
    return `
<div class="eq-popup">
    <div class="eq-header">
        <div class="eq-mag" style="background:${corMagnitude(s.magnitude)}">
            ${s.magnitude ?? "?"}
        </div>

        <div class="eq-title">
            <h3>${s.obsRegion}</h3>
            <small>${s.source}</small>
        </div>
    </div>

    <table class="eq-table">
        <tr>
            <td><i class="bi bi-calendar-event"></i></td>
            <td>Data</td>
            <td>${new Date(s.time).toLocaleString("pt-PT")}</td>
        </tr>
        <tr>
            <td><i class="bi bi-arrow-down-circle"></i></td>
            <td>Profundidade</td>
            <td>${s.depth}</td>
        </tr>
        <tr>
            <td><i class="bi bi-geo-alt"></i></td>
            <td>Latitude</td>
            <td>${Number(s.latitude).toFixed(4)}°</td>
        </tr>
        <tr>
            <td><i class="bi bi-geo"></i></td>
            <td>Longitude</td>
            <td>${Number(s.longitude).toFixed(4)}°</td>
        </tr>
        <tr>
            <td><i class="bi bi-speedometer2"></i></td>
            <td>Intensidade</td>
            <td>${s.intensity ?? "—"}</td>
        </tr>
        <tr>
            <td><i class="bi bi-broadcast"></i></td>
            <td>Origem</td>
            <td>${s.source}</td>
        </tr>
    </table>
</div>`;
}

function criarMarcador(s) {
    const recente = ultimas24h(s.time);
    const sentido = temIntensidade(s);
    const raio = raioMagnitude(s.magnitude);

    let marker = null;

    if (!sentido) {
        // Marcador principal
        marker = L.circleMarker([s.latitude, s.longitude], {
            radius: raio,
            color: "#ffffff",
            weight: recente ? 3 : 1.5,
            fillColor: corMagnitude(s.magnitude),
            fillOpacity: recente ? 1 : 0.9,
            className: recente ? "earthquake-recent" : ""
        });
    } else {
        marker = L.marker([s.latitude, s.longitude], {
            radius: raio,
            color: "#ffffff",
            weight: recente ? 3 : 1.5,
            fillColor: corMagnitude(s.magnitude),
            fillOpacity: recente ? 1 : 0.9,
            className: recente ? "earthquake-recent" : "",
            zIndexOffset: 600,
            icon: L.divIcon({
                className: "sentido-badge",
                html: `
                    <div class="sentido-badge-inner" style="background-color: ${corMagnitude(s.magnitude)}">
                        <h6 style="color: white">${s.intensity}</h6>
                    </div>
                `,
                iconSize: [raio * 2, raio * 2],
                iconAnchor: [raio, raio]
            })
        });

    }

    // Halo de pulsação (sismos recentes)
    if (recente) {
        const tamanho = (raio + 8) * 2;

        const halo = L.marker([s.latitude, s.longitude], {
            interactive: false,
            zIndexOffset: -1000,
            icon: L.divIcon({
                className: "pulse-marker",
                html: `<div class="pulse" style="width:${tamanho}px;height:${tamanho}px"></div>`,
                iconSize: [tamanho, tamanho],
                iconAnchor: [tamanho / 2, tamanho / 2]
            })
        });
        halo.addTo(markersLayer);
    }

    marker.bindPopup(criarPopup(s), {
        maxWidth: 320,
        className: "earthquake-popup"
    });

    marker.on("popupopen", function () {
        const el = this.getPopup().getElement();
        if (!el || typeof enriquecerPopupComCems !== "function") return;
        const content = el.querySelector(".eq-popup");
        if (content && !content.querySelector(".cems-match-banner")) {
            enriquecerPopupComCems(s, content);
        }
    });

    marker.addTo(markersLayer);

    // Área de clique maior
    const hitArea = L.circleMarker([s.latitude, s.longitude], {
        radius: Math.max(raio, 18),
        stroke: false,
        fill: true,
        fillColor: "#ffffff",
        fillOpacity: 0.01
    });
    hitArea.on("click", () => marker.openPopup());
    hitArea.addTo(markersLayer);

    return marker;
}

function atualizarMarcadores(dados) {
    markersLayer.clearLayers();
    state.markers = [];
    state.earthquakes = [];

    const maxMagnitude = dados.data.reduce((max, s) => {
        const magnitude = s.magnitude ?? 0;
        return magnitude > max ? magnitude : max;
    }, 0);

    const heatPoints = [];
    const bounds = [];

    dados.data.forEach((s) => {
        const intensidadeHeat = maxMagnitude > 0
            ? (s.magnitude ?? 0) / maxMagnitude
            : 0;

        heatPoints.push([s.latitude, s.longitude, intensidadeHeat]);
        bounds.push([s.latitude, s.longitude]);

        const marker = criarMarcador(s);

        state.markers.push(marker);
        state.earthquakes.push({
            marker,
            latlng: L.latLng(s.latitude, s.longitude),
            time: new Date(s.time).getTime(),
            data: s
        });
    });

    // Atualiza o intervalo de datas
    if (dados.data.length) {
        const times = dados.data.map(s => new Date(s.time).getTime());
        minDate = Math.min(...times);
        maxDate = Math.max(...times);

        if (window._updateDateRangeUI) {
            window._updateDateRangeUI();
        }
    }

    heatLayer.setLatLngs(heatPoints);
    heatLayer.redraw();

    if (state.heatVisible) {
        if (!map.hasLayer(heatLayer)) {
            heatLayer.addTo(map);
        }
    } else if (map.hasLayer(heatLayer)) {
        map.removeLayer(heatLayer);
    }

    if (state.firstLoad && bounds.length) {
        state.firstLoad = false;
    }
}

function selecionarSismoMaisProximo(e) {
    let maisProximo = null;
    let menorDistancia = Infinity;

    const pontoClique = map.latLngToContainerPoint(e.latlng);

    state.earthquakes.forEach(eq => {
        const pontoSismo = map.latLngToContainerPoint(eq.latlng);
        const distancia = pontoClique.distanceTo(pontoSismo);

        if (distancia < menorDistancia) {
            menorDistancia = distancia;
            maisProximo = eq;
        }
    });

    const tolerancia = window.innerWidth < 768 ? 35 : 20;

    if (maisProximo && menorDistancia <= tolerancia) {
        maisProximo.marker.openPopup();
    }
}

// Guarda as datas extrema dos sismos carregados
let minDate = null;
let maxDate = null;

function initDateRange() {
    const startInput = document.getElementById("range-start");
    const endInput = document.getElementById("range-end");
    const labelStart = document.getElementById("label-start");
    const labelEnd = document.getElementById("label-end");
    const track = document.getElementById("range-track");

    if (!startInput || !endInput) return;

    function updateLabels(e) {
        if (!minDate || !maxDate) return;

        const total = maxDate - minDate;
        let startVal = Number(startInput.value);
        let endVal = Number(endInput.value);

        if (startVal > endVal) {
            if (e && e.target === startInput) {
                endInput.value = startVal;
                endVal = startVal;
            } else {
                startInput.value = endVal;
                startVal = endVal;
            }
        }

        const startTs = minDate + (startVal / 100) * total;
        const endTs = minDate + (endVal / 100) * total;

        labelStart.textContent = formatDate(startTs);
        labelEnd.textContent = formatDate(endTs);

        const top = 100 - endVal;
        const height = endVal - startVal;
        track.style.top = `${top}%`;
        track.style.height = `${height}%`;

        filtrarSismosPorData(startTs, endTs);
    }

    startInput.addEventListener("input", updateLabels);
    endInput.addEventListener("input", updateLabels);

    window._updateDateRangeUI = updateLabels;
}

function formatDate(ts) {
    const d = new Date(ts);
    return d.toLocaleDateString("pt-PT", {
        day: "2-digit",
        month: "2-digit",
    });
}

function filtrarSismosPorData(startTs, endTs) {
    markersLayer.clearLayers();

    const heatPoints = [];
    let maxMagnitude = 0;

    state.earthquakes.forEach(eq => {
        if (eq.time >= startTs && eq.time <= endTs) {
            const mag = eq.data?.magnitude ?? 0;
            if (mag > maxMagnitude) maxMagnitude = mag;
        }
    });

    state.earthquakes.forEach(eq => {
        if (eq.time < startTs || eq.time > endTs) return;

        const s = eq.data;
        criarMarcador(s);   // reutiliza a mesma lógica visual

        // Heatmap
        const intensidade = maxMagnitude > 0
            ? (s.magnitude ?? 0) / maxMagnitude
            : 0;
        heatPoints.push([s.latitude, s.longitude, intensidade]);
    });

    heatLayer.setLatLngs(heatPoints);
    heatLayer.redraw();
}

function ajustarAlturaIntervalo() {
    const wrapper = document.querySelector(".range-wrapper");
    const control = document.querySelector(".intervalo");
    if (!wrapper || !control) return;

    const mapEl = document.getElementById("map");
    const mapHeight = mapEl.clientHeight;

    const topButtons = 110;
    const bottomLegend = 390;
    const padding = 40;

    const available = mapHeight - topButtons - bottomLegend - padding;
    const height = Math.max(120, Math.min(available, 380));

    wrapper.style.height = `${height}px`;
    control.style.height = "auto";
}

function definirIntervalo() {
    const intervalo = L.control({
        position: "topleft"
    });

    intervalo.onAdd = function () {
        const div = L.DomUtil.create("div", "intervalo");
        div.innerHTML = `
            <div class="intervalo-title">
                <i class="bi bi-calendar-range"></i>
            </div>

            <div class="intervalo-content">
                <div class="intervalo-labels">
                    <span>Início</span>
                    <strong id="label-start">—</strong>
                </div>

                <div class="range-wrapper">
                    <div class="range-track" id="range-track"></div>
                    <input type="range" id="range-start" min="0" max="100" value="0" step="0.1">
                    <input type="range" id="range-end"   min="0" max="100" value="100" step="0.1">
                </div>

                <div class="intervalo-labels">
                    <span>Fim</span>
                    <strong id="label-end">—</strong>
                </div>
            </div>
        `;

        L.DomEvent.disableClickPropagation(div);
        L.DomEvent.disableScrollPropagation(div);

        return div;
    };

    intervalo.addTo(map);

    ajustarAlturaIntervalo();
    initDateRange();
}

function definirLegenda() {
    const legenda = L.control({
        position: "bottomleft"
    });

    legenda.onAdd = function () {
        const div = L.DomUtil.create("div", "legend-control");

        div.innerHTML = `
            <button id="legend-btn" title="Legenda" class="legend-toggle">
                <i class="bi bi-info-square-fill"></i>
            </button>

            <div id="legend-panel" class="legend-panel hidden">
                <table class="legend-table">
                    <tr>
                        <td><i class="bi bi-record-circle-fill" style="color:#4a4a4a"></i></td>
                        <td>&lt;2</td>
                    </tr>
                    <tr>
                        <td><i class="bi bi-record-circle-fill" style="color:#7B4B1A"></i></td>
                        <td>&lt;3</td>
                    </tr>
                    <tr>
                        <td><i class="bi bi-record-circle-fill" style="color:#A66A2B"></i></td>
                        <td>&lt;4</td>
                    </tr>
                    <tr>
                        <td><i class="bi bi-record-circle-fill" style="color:#D46A00"></i></td>
                        <td>&lt;5</td>
                    </tr>
                    <tr>
                        <td><i class="bi bi-record-circle-fill" style="color:#E53935"></i></td>
                        <td>&lt;6</td>
                    </tr>
                    <tr>
                        <td><i class="bi bi-record-circle-fill" style="color:#8E0000"></i></td>
                        <td>≥6</td>
                    </tr>
                    <!--
                    <tr>
                        <td>
                            <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" fill="#89aeff" class="bi bi-circle" viewBox="0 0 16 16">
                                <path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14m0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16"/>
                                <circle cx="7.5" cy="7.5" r="7.5" fill="#0e53b9" />
                            </svg>
                        </td>
                        <td>ems</td>
                    </tr>
                    -->
                </table>
            </div>
        `;

        L.DomEvent.disableClickPropagation(div);

        const btn = div.querySelector("#legend-btn");
        const panel = div.querySelector("#legend-panel");

        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            panel.classList.toggle("hidden");
            btn.classList.toggle("active");
        });

        return div;
    };

    legenda.addTo(map);
}

function configurarControles() {
    const MapButtons = L.Control.extend({
        options: {
            position: "topleft"
        },
        onAdd: function () {
            const div = L.DomUtil.create("div", "map-buttons");
            div.innerHTML = `
                <button id="basemap-btn" title="Mapa">
                    <i class="bi bi-sun-fill"></i>
                </button>
                <button id="heat-btn" title="Heatmap">
                    <i class="bi bi-fire"></i>
                </button>
                <button id="cems-btn" title="CEMS Rapid Mapping" class="active">
                    <i>🛰</i>
                </button>`;
            L.DomEvent.disableClickPropagation(div);
            return div;
        }
    });

    map.addControl(new MapButtons());

    document.addEventListener("click", (e) => {
        const heatBtn = e.target.closest("#heat-btn");
        if (heatBtn) {
            state.heatVisible = !state.heatVisible;
            heatBtn.classList.toggle("active", state.heatVisible);

            if (state.heatVisible) {
                heatLayer.addTo(map);
            } else {
                map.removeLayer(heatLayer);
            }
            return;
        }

        const cemsBtn = e.target.closest("#cems-btn");
        if (cemsBtn && window.cemsLayer) {
            if (map.hasLayer(cemsLayer)) {
                map.removeLayer(cemsLayer);
                cemsBtn.classList.remove("active");
            } else {
                cemsLayer.addTo(map);
                cemsBtn.classList.add("active");
            }
            return;
        }

        const basemapBtn = e.target.closest("#basemap-btn");
        if (!basemapBtn) return;

        state.darkMode = !state.darkMode;
        if (state.darkMode) {
            map.removeLayer(basemaps.osm);
            basemaps.dark.addTo(map);
            basemapBtn.innerHTML = '<i class="bi bi-sun-fill"></i>';
            basemapBtn.classList.add("active");
        } else {
            map.removeLayer(basemaps.dark);
            basemaps.osm.addTo(map);
            basemapBtn.innerHTML = '<i class="bi bi-moon-stars-fill"></i>';
            basemapBtn.classList.remove("active");
        }
    });
}

async function carregarPlacas() {
    try {
        const response = await fetch("/static/data/plates.geojson", {
            cache: "no-store"
        });

        if (!response.ok) {
            throw new Error("Erro ao carregar placas tectónicas");
        }

        const geojson = await response.json();
        platesLayer.addData(geojson);
        atualizarVisibilidadePlacas();

    } catch (error) {
        console.error("Falha ao carregar fronteiras das placas:", error);
    }
}

async function carregarSismos() {
    try {
        if (!state.hasLoaded) {
            loadingEl.style.display = "flex";
        }

        const response = await fetch("/api/sismos", {
            cache: "no-store"
        });

        if (!response.ok) {
            throw new Error("Erro ao obter dados.");
        }

        const dados = await response.json();
        totalEl.textContent = dados.total;
        atualizarMarcadores(dados);

        state.hasLoaded = true;
    } catch (error) {
        console.error(error);
        if (!state.hasLoaded) {
            console.error("Não foi possível carregar os dados.");
        }
    } finally {
        loadingEl.style.display = "none";
    }
}

function iniciarAtualizacoes() {
    if (state.refreshTimer) {
        window.clearInterval(state.refreshTimer);
    }

    state.refreshTimer = window.setInterval(() => {
        carregarSismos();
    }, 60000);
}

window.zoom = function (i) {
    const s = state.markers[i]?.getLatLng ? state.markers[i].getLatLng() : null;

    if (!s) return;

    map.flyTo([s.lat, s.lng], 9, {
        duration: 1.2
    });

    state.markers[i].openPopup();
};

function ultimas24h(dataHora) {
    const agora = Date.now();
    const data = new Date(dataHora).getTime();
    return (agora - data) <= 24 * 60 * 60 * 1000;
}

function inicializarMapa() {
    definirLegenda();
    configurarControles();
    definirIntervalo();
    carregarPlacas();
    carregarSismos();
    iniciarAtualizacoes();
    map.on("click", selecionarSismoMaisProximo);
    map.on("zoomend", atualizarVisibilidadePlacas);
    window.addEventListener("resize", ajustarAlturaIntervalo);
    if (typeof carregarCems === "function") {
        carregarCems().then(() => {
            if (window.cemsLayer && !map.hasLayer(cemsLayer)) {
                cemsLayer.addTo(map);
            }
            if (typeof iniciarRefreshCems === "function") {
                iniciarRefreshCems(10 * 60 * 1000);
            }
        });
    }
}

inicializarMapa();