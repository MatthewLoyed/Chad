import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { Map as MapIcon } from 'lucide-react';
import type { MapViewPayload } from '../../utils/mapDisplay';
import './PlacesMapCard.css';

// Default Leaflet marker icons break under Vite bundling — use CDN icons.
const markerIcon = L.icon({
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowSize: [41, 41],
});

interface PlacesMapCardProps {
    view: MapViewPayload;
}

const PlacesMapCard: React.FC<PlacesMapCardProps> = ({ view }) => {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const mapRef = useRef<L.Map | null>(null);
    const placesKey = view.places.map((p) => `${p.lat},${p.lon},${p.name}`).join('|');

    useEffect(() => {
        if (!containerRef.current) return;

        if (mapRef.current) {
            mapRef.current.remove();
            mapRef.current = null;
        }

        const map = L.map(containerRef.current, {
            scrollWheelZoom: false,
            attributionControl: true,
        }).setView([view.center.lat, view.center.lon], view.zoom);

        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        }).addTo(map);

        const bounds: L.LatLngExpression[] = [];
        for (const place of view.places) {
            const marker = L.marker([place.lat, place.lon], { icon: markerIcon }).addTo(map);
            const bits = [
                `<strong>${escapeHtml(place.name)}</strong>`,
                place.category ? `<div class="places-map-popup-meta">${escapeHtml(place.category)}</div>` : '',
                place.address ? `<div class="places-map-popup-meta">${escapeHtml(place.address)}</div>` : '',
                place.osm_url
                    ? `<a href="${escapeAttr(place.osm_url)}" target="_blank" rel="noopener noreferrer">Open in OSM</a>`
                    : '',
            ].filter(Boolean);
            marker.bindPopup(bits.join(''));
            bounds.push([place.lat, place.lon]);
        }

        if (bounds.length > 1) {
            map.fitBounds(bounds as L.LatLngBoundsExpression, { padding: [28, 28], maxZoom: 16 });
        }

        // Leaflet needs a tick after layout to size correctly in chat bubbles.
        requestAnimationFrame(() => map.invalidateSize());
        mapRef.current = map;

        return () => {
            map.remove();
            mapRef.current = null;
        };
        // Intentionally key off primitives — parent often passes a fresh view object each render.
        // eslint-disable-next-line react-hooks/exhaustive-deps -- placesKey encodes marker identity
    }, [view.title, view.center.lat, view.center.lon, view.zoom, placesKey]);

    return (
        <div className="places-map-card">
            <div className="places-map-header">
                <div className="places-map-header-left">
                    <MapIcon className="places-map-brand-icon" size={16} />
                    <span className="places-map-title" title={view.title}>
                        {view.title}
                    </span>
                </div>
                <span className="places-map-count">
                    {view.places.length} place{view.places.length === 1 ? '' : 's'}
                </span>
            </div>
            <div className="places-map-body">
                <div ref={containerRef} className="places-map-leaflet" />
            </div>
            <ul className="places-map-list">
                {view.places.slice(0, 8).map((p, i) => (
                    <li key={`${p.name}-${p.lat}-${p.lon}-${i}`}>
                        <span className="places-map-list-name">{p.name}</span>
                        {p.category ? <span className="places-map-list-cat">{p.category}</span> : null}
                    </li>
                ))}
            </ul>
        </div>
    );
};

function escapeHtml(value: string): string {
    return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function escapeAttr(value: string): string {
    return escapeHtml(value).replace(/'/g, '&#39;');
}

export default PlacesMapCard;
