import type { ThinkingEvent } from '../store';

export type MapPlace = {
    name: string;
    lat: number;
    lon: number;
    category?: string;
    address?: string;
    osm_url?: string;
};

export type MapViewPayload = {
    title: string;
    center: { lat: number; lon: number };
    zoom: number;
    places: MapPlace[];
};

function asNumber(value: unknown): number | null {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim()) {
        const n = Number(value);
        return Number.isFinite(n) ? n : null;
    }
    return null;
}

function coercePlaces(raw: unknown): MapPlace[] {
    if (!Array.isArray(raw)) return [];
    const out: MapPlace[] = [];
    for (const item of raw) {
        if (!item || typeof item !== 'object') continue;
        const p = item as Record<string, unknown>;
        const lat = asNumber(p.lat);
        const lon = asNumber(p.lon);
        if (lat === null || lon === null) continue;
        const name = String(p.name || p.display_name || 'Place').trim();
        out.push({
            name,
            lat,
            lon,
            category: p.category != null ? String(p.category) : undefined,
            address: p.address != null ? String(p.address) : undefined,
            osm_url: p.osm_url != null ? String(p.osm_url) : undefined,
        });
    }
    return out.slice(0, 50);
}

/** Resolved OpenStreetMap card payload from a show_map tool event. */
export function resolveMapDisplay(event: ThinkingEvent): MapViewPayload | null {
    if (event.name !== 'show_map') return null;
    if (event.success !== true) return null;

    const fromEvent = event.map_view;
    const params = event.params ?? {};

    let title = '';
    let centerLat: number | null = null;
    let centerLon: number | null = null;
    let zoom = 14;
    let places: MapPlace[] = [];

    if (fromEvent && typeof fromEvent === 'object') {
        title = String(fromEvent.title || '').trim();
        centerLat = asNumber(fromEvent.center?.lat);
        centerLon = asNumber(fromEvent.center?.lon);
        zoom = asNumber(fromEvent.zoom) ?? 14;
        places = coercePlaces(fromEvent.places);
    }

    if (!places.length) {
        places = coercePlaces(params.places);
    }
    if (!title) {
        title = String(params.title || '').trim();
    }
    if (centerLat === null) centerLat = asNumber(params.center_lat);
    if (centerLon === null) centerLon = asNumber(params.center_lon);
    if (centerLat === null && places[0]) centerLat = places[0].lat;
    if (centerLon === null && places[0]) centerLon = places[0].lon;

    if (!title || centerLat === null || centerLon === null || !places.length) {
        return null;
    }

    return {
        title,
        center: { lat: centerLat, lon: centerLon },
        zoom: Math.max(1, Math.min(Math.round(zoom), 19)),
        places,
    };
}

export function isMapDisplayTool(event: ThinkingEvent): boolean {
    return event.name === 'show_map';
}
