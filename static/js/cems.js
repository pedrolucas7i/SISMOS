//===================================================
// COPERNICUS EMS Rapid Mapping — camada Leaflet
//===================================================
// Depende de: map (global), L (Leaflet)
// Endpoint: GET /api/cems  e  GET /api/cems/<code>

const cemsLayer = L.layerGroup();

const cemsState = {
    loaded: false,
    activations: [],
    visible: true,
    refreshTimer: null,
};

const CEMS_ICON = L.divIcon({
    className: "cems-marker",
    html: `
        <div class="cems-marker-inner" title="Copernicus EMS">
            <i class="bi bi-satellite"></i>
        </div>
    `,
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -14],
});

function formatCemsDate(iso) {
    if (!iso) return "—";
    try {
        return new Date(iso).toLocaleString("pt-PT", {
            day: "2-digit",
            month: "2-digit",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
        });
    } catch {
        return iso;
    }
}

function countriesLabel(countries) {
    if (!countries || !countries.length) return "—";
    return countries
        .map((c) => (typeof c === "string" ? c : c.name || c.short_name || ""))
        .filter(Boolean)
        .join(", ");
}

function criarPopupCems(act, detailSummary) {
    const totals = (detailSummary && detailSummary.totals) || {};
    const buildings = totals.buildings_affected;
    const finished = totals.products_finished;
    const pending = totals.products_pending;
    const report = (detailSummary && detailSummary.reportLink) || null;

    const damageRows =
        buildings != null && buildings > 0
            ? `<tr>
                <td><i class="bi bi-building"></i></td>
                <td>Edifícios afetados</td>
                <td>${buildings}</td>
               </tr>`
            : "";

    const productsRow =
        finished != null || pending != null
            ? `<tr>
                <td><i class="bi bi-map"></i></td>
                <td>Produtos</td>
                <td>${finished ?? 0} prontos${pending ? ` · ${pending} pendentes` : ""}</td>
               </tr>`
            : "";

    const reportLink = report
        ? `<tr>
            <td><i class="bi bi-journal-text"></i></td>
            <td>Relatório</td>
            <td><a href="${report}" target="_blank" rel="noopener">StoryMap</a></td>
           </tr>`
        : "";

    return `
<div class="eq-popup cems-popup">
    <div class="eq-header cems-header">
        <div class="eq-mag cems-badge">
            <i class="bi bi-satellite"></i>
        </div>
        <div class="eq-title">
            <h3>${act.name || act.code}</h3>
            <small>Copernicus EMS · ${act.code}</small>
        </div>
    </div>
    <table class="eq-table">
        <tr>
            <td><i class="bi bi-tag"></i></td>
            <td>Categoria</td>
            <td>${act.category || "Earthquake"}</td>
        </tr>
        <tr>
            <td><i class="bi bi-calendar-event"></i></td>
            <td>Evento</td>
            <td>${formatCemsDate(act.eventTime)}</td>
        </tr>
        <tr>
            <td><i class="bi bi-lightning-charge"></i></td>
            <td>Ativação</td>
            <td>${formatCemsDate(act.activationTime)}</td>
        </tr>
        <tr>
            <td><i class="bi bi-globe"></i></td>
            <td>Países</td>
            <td>${countriesLabel(act.countries)}</td>
        </tr>
        <tr>
            <td><i class="bi bi-info-circle"></i></td>
            <td>Estado</td>
            <td>${act.closed ? "Encerrada" : "Aberta"}</td>
        </tr>
        ${damageRows}
        ${productsRow}
        ${reportLink}
        <tr>
            <td><i class="bi bi-box-arrow-up-right"></i></td>
            <td>Portal</td>
            <td><a href="${act.portal_url}" target="_blank" rel="noopener">Abrir EMSR</a></td>
        </tr>
    </table>
</div>`;
}

function addCemsMarker(act) {
    const lat = act.centroid_lat;
    const lon = act.centroid_lon;
    if (lat == null || lon == null) return null;

    const marker = L.marker([lat, lon], {
        icon: CEMS_ICON,
        zIndexOffset: 800,
        title: `CEMS ${act.code}`,
    });

    // Popup leve primeiro; enriquece com detalhe sob demanda
    marker.bindPopup(criarPopupCems(act, null), {
        maxWidth: 340,
        className: "earthquake-popup cems-popup-wrapper",
    });

    marker.on("popupopen", async () => {
        try {
            const r = await fetch(`/api/cems/${encodeURIComponent(act.code)}`);
            if (!r.ok) return;
            const payload = await r.json();
            marker.setPopupContent(criarPopupCems(act, payload.summary));
        } catch (e) {
            console.warn("CEMS detail:", e);
        }
    });

    marker.addTo(cemsLayer);
    return marker;
}

async function carregarCems(options = {}) {
    const relevant = options.relevant !== false; // default: preferir relevantes
    const qs = relevant ? "?relevant=0" : ""; // mostrar todos os sismos CEMS no mapa mundial
    // Nota: relevant=0 lista todos os sismos CEMS; o mapa de PT continua centrado em PT
    try {
        const r = await fetch(`/api/cems${qs}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const payload = await r.json();
        const acts = payload.activations || [];

        cemsLayer.clearLayers();
        cemsState.activations = acts;

        acts.forEach(addCemsMarker);

        cemsState.loaded = true;
        atualizarContadorCems(acts.length);
        return acts;
    } catch (e) {
        console.warn("Falha ao carregar CEMS:", e);
        return [];
    }
}

function atualizarContadorCems(n) {
    const el = document.getElementById("total-cems");
    if (el) el.textContent = String(n ?? cemsState.activations.length);
}

function iniciarRefreshCems(intervalMs = 10 * 60 * 1000) {
    if (cemsState.refreshTimer) clearInterval(cemsState.refreshTimer);
    cemsState.refreshTimer = setInterval(() => {
        carregarCems();
    }, intervalMs);
}

/**
 * Enriquece o popup de um sismo IPMA com link CEMS se houver match.
 * Chamar a partir de criarPopup / ao abrir popup, se quiseres.
 */
async function enriquecerPopupComCems(sismo, popupElement) {
    if (!sismo || sismo.latitude == null || sismo.longitude == null) return;
    try {
        const params = new URLSearchParams({
            lat: sismo.latitude,
            lon: sismo.longitude,
            radius_km: "300",
            hours: "96",
        });
        if (sismo.time) params.set("time", sismo.time);

        const r = await fetch(`/api/cems/match?${params}`);
        if (!r.ok) return;
        const data = await r.json();
        if (!data.match) return;

        const m = data.match;
        const box = document.createElement("div");
        box.className = "cems-match-banner";
        box.innerHTML = `
            <i class="bi bi-satellite"></i>
            <span>
                CEMS <strong>${m.code}</strong>
                · ${m.distance_km != null ? m.distance_km + " km" : ""}
                <a href="${m.portal_url}" target="_blank" rel="noopener">ver mapas de dano</a>
            </span>
        `;
        if (popupElement) {
            popupElement.appendChild(box);
        }
    } catch (e) {
        /* silencioso */
    }
}

// Exporta para map.js / consola
window.cemsLayer = cemsLayer;
window.carregarCems = carregarCems;
window.iniciarRefreshCems = iniciarRefreshCems;
window.enriquecerPopupComCems = enriquecerPopupComCems;
