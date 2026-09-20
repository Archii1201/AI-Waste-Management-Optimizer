import { useEffect } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import { EmptyState, FILL_COLORS, ROUTE_COLORS, fmt } from "./ui.jsx";

function MapFocus({ target }) {
  const map = useMap();
  useEffect(() => {
    if (!target) return;
    map.flyTo([target.lat, target.lng], 16, { duration: 0.6 });
  }, [map, target]);
  return null;
}

function routePath(route, binsById) {
  return (route.stops || [])
    .slice()
    .sort((a, b) => a.sequence - b.sequence)
    .map((stop) => binsById.get(stop.bin_id))
    .filter(Boolean)
    .map((bin) => [bin.latitude, bin.longitude]);
}

export default function BinMap({
  bins,
  reference,
  routes = [],
  selectedRouteId,
  selectedBinId,
  focusTarget,
  onSelectBin,
}) {
  const center = reference?.map?.center || [19.076, 72.8777];
  const city = reference?.map?.city || "Mumbai";
  const binsById = new Map(bins.map((bin) => [bin.id, bin]));

  return (
    <section className="card">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold">Bin map · {city}</h2>
        <p className="muted">{bins.length} bins</p>
      </div>
      {bins.length === 0 ? (
        <EmptyState label="No bins with coordinates to plot." />
      ) : (
        <div className="h-[28rem] overflow-hidden rounded-xl">
          <MapContainer center={center} zoom={12} style={{ height: "100%", width: "100%" }}>
            <TileLayer
              attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            />
            <MapFocus target={focusTarget} />
            {routes.map((route, index) => {
              const path = routePath(route, binsById);
              if (path.length < 2) return null;
              const selected = route.id === selectedRouteId;
              return (
                <Polyline
                  key={route.id}
                  positions={path}
                  pathOptions={{
                    color: selected ? "#ffffff" : ROUTE_COLORS[index % ROUTE_COLORS.length],
                    weight: selected ? 5 : 3,
                    opacity: selected || !selectedRouteId ? 0.9 : 0.25,
                  }}
                />
              );
            })}
            {bins.map((bin) => {
              const selected = bin.id === selectedBinId;
              return (
                <CircleMarker
                  key={bin.id}
                  center={[bin.latitude, bin.longitude]}
                  radius={selected ? 11 : 8}
                  eventHandlers={{ click: () => onSelectBin(bin) }}
                  pathOptions={{
                    color: selected ? "#ffffff" : FILL_COLORS[bin.fill_status] || FILL_COLORS.medium,
                    fillColor: FILL_COLORS[bin.fill_status] || FILL_COLORS.medium,
                    fillOpacity: 0.85,
                    weight: selected ? 2 : 1,
                  }}
                >
                  <Tooltip direction="top" offset={[0, -4]} opacity={0.95}>
                    <span>
                      {bin.code} · {fmt(bin.current_fill_level, 0, "%")}
                    </span>
                  </Tooltip>
                </CircleMarker>
              );
            })}
          </MapContainer>
        </div>
      )}
    </section>
  );
}
