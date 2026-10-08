"""
Flask routes for Copernicus integration.
Import and register in app.py:

    from copernicus_routes import register_copernicus_routes
    register_copernicus_routes(app)
"""

from __future__ import annotations

from flask import Flask, jsonify, request

from copernicus_cems import (
    fetch_activation_detail,
    filter_earthquake_activations,
    find_matching_activation_for_sismo,
    summarise_damage,
)


def register_copernicus_routes(app: Flask) -> None:
    @app.get("/api/cems")
    def api_cems_list():
        """
        Lista ativações Rapid Mapping de sismos.
        Query:
          ?relevant=1   → só países relevantes (PT/ES/MA/…)
          ?open=1       → só ativações abertas
        """
        relevant = request.args.get("relevant", "0") in ("1", "true", "yes")
        open_only = request.args.get("open", "0") in ("1", "true", "yes")
        data = filter_earthquake_activations(
            only_relevant_geo=relevant,
            include_open_only=open_only,
        )
        return jsonify({
            "source": "Copernicus EMS Rapid Mapping",
            "count": len(data),
            "activations": data,
        })

    @app.get("/api/cems/<code>")
    def api_cems_detail(code: str):
        """Detalhe de uma ativação (AOIs, produtos, stats de dano)."""
        detail = fetch_activation_detail(code)
        if not detail:
            return jsonify({"error": "activation not found", "code": code}), 404
        summary = summarise_damage(detail)
        return jsonify({
            "source": "Copernicus EMS Rapid Mapping",
            "summary": summary,
            "raw": {
                "code": detail.get("code"),
                "name": detail.get("name"),
                "reason": detail.get("reason"),
                "category": detail.get("category"),
                "eventTime": detail.get("eventTime"),
                "activationTime": detail.get("activationTime"),
                "closed": detail.get("closed"),
                "reportLink": detail.get("reportLink"),
                "productsPath": detail.get("productsPath"),
                "gdacsId": detail.get("gdacsId"),
            },
        })

    @app.get("/api/cems/match")
    def api_cems_match():
        """
        Cruza um epicentro IPMA com ativações CEMS próximas.
        Query obrigatória: lat, lon
        Opcional: time (ISO), radius_km (default 250), hours (default 72)
        """
        try:
            lat = float(request.args["lat"])
            lon = float(request.args["lon"])
        except (KeyError, ValueError, TypeError):
            return jsonify({
                "error": "lat and lon query params required (float)"
            }), 400

        time_iso = request.args.get("time")
        radius = float(request.args.get("radius_km", 250))
        hours = float(request.args.get("hours", 72))

        match = find_matching_activation_for_sismo(
            lat=lat,
            lon=lon,
            event_time_iso=time_iso,
            max_distance_km=radius,
            max_time_hours=hours,
        )
        if not match:
            return jsonify({"match": None, "message": "no nearby CEMS earthquake activation"})
        return jsonify({"match": match})
