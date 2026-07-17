import { useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Html, Line, OrbitControls, RoundedBox } from "@react-three/drei";
import type { OrbitControls as OrbitControlsImpl } from "three-stdlib";
import { Vector3 } from "three";
import type { DashboardMap, DashboardV2ImpactScene, MapBuilding, MapRoad } from "./data/schema";
import { buildingHoverInfo, roadHoverInfo, routeVisual, type MapHoverInfo } from "./mapInfo";

export function ContextCityMap({
  dashboardMap,
  impactScene,
  impactPaths,
  selectedId,
  hoverInfo,
  cameraSignal,
  demoMode,
  onSelect,
  onHover
}: {
  dashboardMap: DashboardMap;
  impactScene: DashboardV2ImpactScene | null;
  impactPaths: Set<string>;
  selectedId: string;
  hoverInfo: MapHoverInfo | null;
  cameraSignal: number;
  demoMode: boolean;
  onSelect: (id: string) => void;
  onHover: (info: MapHoverInfo | null) => void;
}) {
  const reducedMotion = useReducedMotion();
  const controlsRef = useRef<OrbitControlsImpl | null>(null);
  const [tourPaused, setTourPaused] = useState(false);
  useEffect(() => {
    controlsRef.current?.reset();
    setTourPaused(false);
  }, [cameraSignal]);

  return (
    <div className="city-canvas-wrap">
      {demoMode ? (
        <button type="button" className="city-tour-toggle" onClick={() => setTourPaused((value) => !value)}>
          {tourPaused ? "Resume tour" : "Pause tour"}
        </button>
      ) : null}
      <Canvas shadows camera={{ position: [122, 118, 172], fov: 34 }} dpr={[1, 1.6]} gl={{ antialias: true, alpha: true, preserveDrawingBuffer: true }}>
        <color attach="background" args={["#08111f"]} />
        <ambientLight intensity={0.62} />
        <directionalLight castShadow position={[34, 54, 34]} intensity={1.18} />
        <CityScene dashboardMap={dashboardMap} impactScene={impactScene} impactPaths={impactPaths} selectedId={selectedId} hoverInfo={hoverInfo} reducedMotion={reducedMotion} demoMode={demoMode && !tourPaused} onSelect={onSelect} onHover={onHover} />
        <OrbitControls ref={controlsRef} makeDefault target={[32, 5, 22]} enableDamping={!reducedMotion} dampingFactor={0.08} minDistance={24} maxDistance={360} maxPolarAngle={Math.PI / 2.08} />
      </Canvas>
    </div>
  );
}

function CityScene({
  dashboardMap,
  impactScene,
  impactPaths,
  selectedId,
  hoverInfo,
  reducedMotion,
  demoMode,
  onSelect,
  onHover
}: {
  dashboardMap: DashboardMap;
  impactScene: DashboardV2ImpactScene | null;
  impactPaths: Set<string>;
  selectedId: string;
  hoverInfo: MapHoverInfo | null;
  reducedMotion: boolean;
  demoMode: boolean;
  onSelect: (id: string) => void;
  onHover: (info: MapHoverInfo | null) => void;
}) {
  const center = useMemo(() => mapCenter(dashboardMap), [dashboardMap]);
  const points = useMemo(() => mapPoints(dashboardMap), [dashboardMap]);
  const tourRef = useRef<any>(null);
  const { camera, controls } = useThree();
  const sceneEntities = useMemo(() => new Map((impactScene?.entities || []).map((entity) => [entity.id, entity])), [impactScene]);
  useEffect(() => {
    const entity = sceneEntities.get(selectedId);
    if (!entity) return;
    const target = { x: entity.x - center.x, y: Math.max(1, entity.y), z: entity.z - center.z };
    controlsTarget(camera, controls as OrbitControlsImpl | undefined, target, reducedMotion);
  }, [camera, center.x, center.z, controls, reducedMotion, sceneEntities, selectedId]);
  useFrame(({ clock }) => {
    if (!tourRef.current || reducedMotion || !demoMode) return;
    const radius = Math.max(center.width, center.depth, 90) * 0.72;
    tourRef.current.rotation.y = clock.elapsedTime * 0.045;
    tourRef.current.position.x = Math.sin(clock.elapsedTime * 0.045) * radius * 0.05;
    tourRef.current.position.z = Math.cos(clock.elapsedTime * 0.045) * radius * 0.05;
  });
  return (
    <group ref={tourRef} position={[-center.x, 0, -center.z]}>
      <mesh position={[center.x, -0.08, center.z]} receiveShadow>
        <boxGeometry args={[Math.max(42, center.width + 42), 0.12, Math.max(34, center.depth + 42)]} />
        <meshStandardMaterial color="#0f1b2d" roughness={0.92} metalness={0.05} />
      </mesh>
      {dashboardMap.districts.map((district) => (
        <group key={district.id}>
          <mesh position={[district.x + 8, 0.02, district.z + 8]} rotation={[0, Math.PI / 8, 0]}>
            <cylinderGeometry args={[24, 24, 0.1, 8]} />
            <meshStandardMaterial color={district.selected_count ? "#182c46" : "#131f30"} roughness={0.9} />
          </mesh>
          <Html position={[district.x + 4, 0.35, district.z - 14]} center className="district-label">
            {district.label}
          </Html>
        </group>
      ))}
      {dashboardMap.roads.slice(0, 100).map((road) => (
        <RoadMesh key={road.id} road={road} points={points} onSelect={onSelect} onHover={onHover} />
      ))}
      {(impactScene?.relationships || []).filter((relationship) => relationship.task_relevant).slice(0, 120).map((relationship) => {
        const source = sceneEntities.get(relationship.source_id);
        const target = sceneEntities.get(relationship.target_id);
        if (!source || !target) return null;
        return <Line key={relationship.id} points={[[source.x, source.y, source.z], [target.x, target.y, target.z]]} color={relationship.id === selectedId ? "#c5f36b" : "#5d8e91"} lineWidth={relationship.id === selectedId ? 3 : Math.max(1, relationship.strength * 2)} transparent opacity={relationship.task_relevant ? 0.75 : 0.3} onClick={(event) => { event.stopPropagation(); onSelect(relationship.id); }} />;
      })}
      {dashboardMap.landmarks.map((landmark) => (
        <group key={landmark.id} position={[landmark.x, 0, landmark.z]}>
          <mesh>
            <cylinderGeometry args={landmark.type === "action" ? [0.72, 0.72, 0.7, 16] : [1.6, 1.6, 1.8, 18]} />
            <meshStandardMaterial color={landmark.tone === "risk" ? "#ff7a7f" : landmark.tone === "good" ? "#6ed49a" : "#80a9ff"} emissive="#1b355d" emissiveIntensity={0.28} />
          </mesh>
          {landmark.type === "action" ? null : (
            <Html position={[0, 2.4, 0]} center className="district-label">
              {landmark.label}
            </Html>
          )}
        </group>
      ))}
      {hoverInfo ? <MapSceneTooltip info={hoverInfo} /> : null}
      {dashboardMap.buildings.map((building) => (
        <BuildingMesh key={building.id} building={building} impacted={impactPaths.has(building.path)} selected={building.node_id === selectedId} reducedMotion={reducedMotion} onSelect={onSelect} onHover={onHover} />
      ))}
      {(impactScene?.entities || []).filter((entity) => entity.kind !== "file").slice(0, 240).map((entity) => (
        <group key={entity.id} position={[entity.x, entity.y, entity.z]} onClick={(event) => { event.stopPropagation(); onSelect(entity.id); }}>
          <mesh castShadow>
            {entity.kind === "test" ? <octahedronGeometry args={[1.05, 0]} /> : entity.kind === "action" ? <cylinderGeometry args={[0.7, 0.9, 1.6, 8]} /> : <sphereGeometry args={[entity.task_relevant ? 0.72 : 0.48, 12, 10]} />}
            <meshStandardMaterial color={entity.id === selectedId ? "#c5f36b" : entity.kind === "test" ? "#ffbd6e" : entity.kind === "action" ? "#8be7d5" : "#b9d5ff"} emissive={entity.task_relevant ? "#315f59" : "#13262c"} emissiveIntensity={entity.task_relevant ? 0.7 : 0.25} />
          </mesh>
          {entity.id === selectedId ? <Html position={[0, 1.7, 0]} center className="district-label">{entity.label}</Html> : null}
        </group>
      ))}
    </group>
  );
}

function controlsTarget(camera: any, controls: OrbitControlsImpl | undefined, target: { x: number; y: number; z: number }, reducedMotion: boolean) {
  const distance = Math.max(18, camera.position.distanceTo(new Vector3(target.x, target.y, target.z)));
  const next = [target.x + distance * 0.45, target.y + distance * 0.35, target.z + distance * 0.55];
  if (reducedMotion) camera.position.set(...next);
  else {
    camera.position.x += (next[0] - camera.position.x) * 0.65;
    camera.position.y += (next[1] - camera.position.y) * 0.65;
    camera.position.z += (next[2] - camera.position.z) * 0.65;
  }
  controls?.target.set(target.x, target.y, target.z);
  controls?.update();
  camera.lookAt(target.x, target.y, target.z);
}

function BuildingMesh({
  building,
  impacted,
  selected,
  reducedMotion,
  onSelect,
  onHover
}: {
  building: MapBuilding;
  impacted: boolean;
  selected: boolean;
  reducedMotion: boolean;
  onSelect: (id: string) => void;
  onHover: (info: MapHoverInfo | null) => void;
}) {
  const ref = useRef<any>(null);
  const service = building.building_tier === "service";
  const pavilion = building.building_tier === "pavilion";
  const width = (service ? 6.2 : 4.6) + building.confidence * 3.8 + (building.selected ? 0.45 : 0);
  const depth = (service ? 5.4 : 4.2) + building.confidence * 3.2 + (building.memory_linked ? 0.28 : 0);
  const towerHeight = pavilion ? 4.2 + building.confidence * 8 : service ? 5.2 + building.confidence * 10 : 4.8 + building.confidence * 18;
  const podiumHeight = 1.15 + building.confidence * 0.5;
  const upperHeight = building.building_tier === "tower" ? towerHeight * 0.34 : 0;
  const floors = Math.min(8, Math.max(3, Math.round(towerHeight / 2.45)));
  const windowColumns = Math.min(4, Math.max(2, Math.round(width / 2.15)));
  const accentColor = building.memory_linked ? "#38cfd3" : selected ? "#80a9ff" : impacted ? "#b7f38e" : "#d9e7ff";
  const roofColor = selected ? "#dbe8ff" : building.memory_linked ? "#b8f7f3" : "#d6e1ef";
  const plazaRadius = Math.max(width, depth) * 0.86;
  const facadeColor = building.color;
  useFrame(({ clock }) => {
    if (!ref.current || reducedMotion || !selected) return;
    ref.current.position.y = Math.sin(clock.elapsedTime * 2.4) * 0.18;
  });
  return (
    <group
      ref={ref}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(building.node_id);
      }}
      onPointerOver={(event) => {
        event.stopPropagation();
        onHover(buildingHoverInfo(building));
      }}
      onPointerOut={(event) => {
        event.stopPropagation();
        onHover(null);
      }}
    >
      {selected ? <GlowDisk x={building.x} z={building.z} radius={plazaRadius * 1.08} color="#80a9ff" opacity={0.32} /> : null}
      {building.selected || impacted ? <GlowDisk x={building.x} z={building.z} radius={plazaRadius * (impacted ? 0.76 : 0.9)} color={accentColor} opacity={impacted ? 0.15 : 0.22} /> : null}
      <mesh position={[building.x, 0.11, building.z]} rotation={[0, Math.PI / 8, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[plazaRadius, plazaRadius * 1.08, 0.22, 8]} />
        <meshStandardMaterial color="#17263b" roughness={0.82} metalness={0.08} />
      </mesh>
      <RoundedBox castShadow receiveShadow args={[width + 1.25, podiumHeight, depth + 1.1]} radius={0.22} smoothness={5} position={[building.x, podiumHeight / 2 + 0.22, building.z]}>
        <meshStandardMaterial color="#213552" roughness={0.72} metalness={0.1} emissive={selected ? "#14396a" : "#000000"} emissiveIntensity={selected ? 0.12 : 0} />
      </RoundedBox>
      <RoundedBox castShadow receiveShadow args={[width, towerHeight, depth]} radius={service ? 0.42 : 0.18} smoothness={5} position={[building.x, podiumHeight + 0.22 + towerHeight / 2, building.z]}>
        <meshStandardMaterial color={facadeColor} roughness={0.58} metalness={0.16} emissive={selected ? "#284f8f" : building.memory_linked ? "#0d4f55" : "#000000"} emissiveIntensity={selected ? 0.2 : building.memory_linked ? 0.16 : 0} />
      </RoundedBox>
      {upperHeight ? (
        <RoundedBox castShadow receiveShadow args={[width * 0.72, upperHeight, depth * 0.72]} radius={0.14} smoothness={5} position={[building.x, podiumHeight + 0.22 + towerHeight + upperHeight / 2, building.z]}>
          <meshStandardMaterial color={facadeColor} roughness={0.54} metalness={0.18} emissive={selected ? "#284f8f" : "#000000"} emissiveIntensity={selected ? 0.16 : 0} />
        </RoundedBox>
      ) : null}
      <mesh position={[building.x, podiumHeight + 0.22 + towerHeight + upperHeight + 0.12, building.z]}>
        <boxGeometry args={[width * 0.86, 0.24, depth * 0.86]} />
        <meshStandardMaterial color={roofColor} roughness={0.44} metalness={0.22} emissive={accentColor} emissiveIntensity={selected || building.memory_linked ? 0.18 : 0.04} />
      </mesh>
      <mesh position={[building.x - width * 0.24, podiumHeight + 0.22 + towerHeight + upperHeight + 0.31, building.z + depth * 0.18]}>
        <boxGeometry args={[width * 0.28, 0.12, depth * 0.3]} />
        <meshStandardMaterial color="#6ed49a" roughness={0.8} metalness={0.02} />
      </mesh>
      <mesh position={[building.x + width * 0.23, podiumHeight + 0.22 + towerHeight + upperHeight + 0.31, building.z - depth * 0.18]}>
        <boxGeometry args={[width * 0.34, 0.1, depth * 0.22]} />
        <meshStandardMaterial color="#9fb0c5" roughness={0.5} metalness={0.28} />
      </mesh>
      {building.confidence >= 0.48 && !service ? (
        <RoundedBox castShadow receiveShadow args={[width * 0.38, towerHeight * 0.46, depth * 0.34]} radius={0.12} smoothness={4} position={[building.x + width * 0.68, podiumHeight + 0.22 + towerHeight * 0.32, building.z - depth * 0.1]}>
          <meshStandardMaterial color={facadeColor} roughness={0.62} metalness={0.12} emissive={building.memory_linked ? "#0d4f55" : "#000000"} emissiveIntensity={building.memory_linked ? 0.1 : 0} />
        </RoundedBox>
      ) : null}
      {Array.from({ length: floors }).flatMap((_, row) =>
        Array.from({ length: windowColumns }).map((__, column) => {
          const x = building.x - width * 0.32 + (column * width * 0.64) / Math.max(1, windowColumns - 1);
          const y = podiumHeight + 1.25 + row * Math.max(1.15, towerHeight / (floors + 1));
          return (
            <mesh key={`${building.id}:front-window:${row}:${column}`} position={[x, y, building.z + depth / 2 + 0.018]}>
              <boxGeometry args={[0.34, 0.3, 0.035]} />
              <meshBasicMaterial color={accentColor} transparent opacity={selected || building.memory_linked ? 0.78 : 0.5} />
            </mesh>
          );
        })
      )}
      <mesh position={[building.x, podiumHeight + 0.65, building.z + depth / 2 + 0.04]}>
        <boxGeometry args={[Math.max(0.9, width * 0.18), 0.78, 0.08]} />
        <meshBasicMaterial color="#101a2b" transparent opacity={0.9} />
      </mesh>
      {building.risk === "high" || building.risk === "medium" ? (
        <mesh position={[building.x - width / 2 - 0.022, podiumHeight + 0.22 + towerHeight * 0.52, building.z]} rotation={[0, Math.PI / 2, 0]}>
          <boxGeometry args={[depth * 0.72, 0.16, 0.035]} />
          <meshBasicMaterial color={building.risk === "high" ? "#ff7a7f" : "#f7cf62"} transparent opacity={0.72} />
        </mesh>
      ) : null}
      <mesh position={[building.x, podiumHeight + 0.22 + towerHeight + upperHeight + 1.6, building.z]}>
        <cylinderGeometry args={[0.045, 0.045, 2.8 + building.confidence * 2, 10]} />
        <meshBasicMaterial color={accentColor} transparent opacity={0.74} />
      </mesh>
      <mesh position={[building.x, podiumHeight + 0.22 + towerHeight + upperHeight + 3.15 + building.confidence * 2, building.z]}>
        <sphereGeometry args={[0.2, 12, 8]} />
        <meshBasicMaterial color={accentColor} transparent opacity={selected || building.memory_linked ? 0.95 : 0.56} />
      </mesh>
    </group>
  );
}

function GlowDisk({ x, z, radius, color, opacity }: { x: number; z: number; radius: number; color: string; opacity: number }) {
  return (
    <mesh position={[x, 0.05, z]}>
      <cylinderGeometry args={[radius, radius, 0.08, 56]} />
      <meshBasicMaterial color={color} transparent opacity={opacity} />
    </mesh>
  );
}

function RoadMesh({
  road,
  points,
  onSelect,
  onHover
}: {
  road: MapRoad;
  points: Map<string, { x: number; z: number }>;
  onSelect: (id: string) => void;
  onHover: (info: MapHoverInfo | null) => void;
}) {
  const source = points.get(road.source);
  const target = points.get(road.target);
  if (!source || !target) return null;
  const sx = source.x;
  const sz = source.z;
  const tx = target.x;
  const tz = target.z;
  const dx = tx - sx;
  const dz = tz - sz;
  const length = Math.sqrt(dx * dx + dz * dz);
  const angle = Math.atan2(dz, dx);
  const visual = routeVisual(road);
  const midpoint: [number, number, number] = [sx + dx / 2, visual.y + 0.75, sz + dz / 2];
  const dashCount = Math.max(3, Math.min(18, Math.floor(length / 8)));
  const dashSpacing = length / dashCount;
  const expressway = visual.label === "expressway";
  const highway = visual.label === "highway";
  return (
    <group
      position={[sx + dx / 2, visual.y, sz + dz / 2]}
      rotation={[0, -angle, 0]}
      onClick={(event) => {
        event.stopPropagation();
        onSelect(road.id);
      }}
      onPointerOver={(event) => {
        event.stopPropagation();
        onHover(roadHoverInfo(road, midpoint));
      }}
      onPointerOut={(event) => {
        event.stopPropagation();
        onHover(null);
      }}
    >
      <mesh>
        <boxGeometry args={[length, visual.height, visual.width]} />
        <meshBasicMaterial color={visual.color} transparent opacity={visual.opacity} />
      </mesh>
      {expressway || highway ? (
        <>
          <mesh position={[0, visual.height / 2 + 0.012, 0]}>
            <boxGeometry args={[length, 0.025, expressway ? 0.08 : 0.05]} />
            <meshBasicMaterial color={expressway ? "#f7cf62" : "#c8d8f3"} transparent opacity={expressway ? 0.74 : 0.48} />
          </mesh>
          {expressway ? (
            <>
              <mesh position={[0, visual.height / 2 + 0.018, visual.width * 0.34]}>
                <boxGeometry args={[length, 0.02, 0.045]} />
                <meshBasicMaterial color="#dce8ff" transparent opacity={0.62} />
              </mesh>
              <mesh position={[0, visual.height / 2 + 0.018, -visual.width * 0.34]}>
                <boxGeometry args={[length, 0.02, 0.045]} />
                <meshBasicMaterial color="#dce8ff" transparent opacity={0.62} />
              </mesh>
            </>
          ) : null}
        </>
      ) : null}
      {visual.label !== "local street"
        ? Array.from({ length: dashCount }).map((_, index) => (
            <mesh key={`${road.id}:dash:${index}`} position={[-length / 2 + dashSpacing * index + dashSpacing * 0.35, visual.height / 2 + 0.026, expressway ? visual.width * 0.17 : 0]}>
              <boxGeometry args={[Math.max(0.9, dashSpacing * 0.38), 0.018, 0.04]} />
              <meshBasicMaterial color="#e7eefc" transparent opacity={expressway ? 0.64 : 0.54} />
            </mesh>
          ))
        : null}
      {expressway
        ? Array.from({ length: dashCount }).map((_, index) => (
            <mesh key={`${road.id}:dash-opposite:${index}`} position={[-length / 2 + dashSpacing * index + dashSpacing * 0.35, visual.height / 2 + 0.026, -visual.width * 0.17]}>
              <boxGeometry args={[Math.max(0.9, dashSpacing * 0.38), 0.018, 0.04]} />
              <meshBasicMaterial color="#e7eefc" transparent opacity={0.64} />
            </mesh>
          ))
        : null}
    </group>
  );
}

function MapSceneTooltip({ info }: { info: MapHoverInfo }) {
  return (
    <Html position={info.position} center className="map-scene-tooltip">
      <span className={`badge ${riskTone(info.tone)}`}>{info.kind}</span>
      <strong>{info.title}</strong>
      <small>{info.subtitle}</small>
      <dl>
        {info.rows.slice(0, 6).map((row) => (
          <div key={`${row.label}:${row.value}`}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </Html>
  );
}

function mapPoints(dashboardMap: DashboardMap) {
  const points = new Map<string, { x: number; z: number }>();
  dashboardMap.buildings.forEach((building) => {
    points.set(building.id, { x: building.x, z: building.z });
    points.set(building.node_id, { x: building.x, z: building.z });
  });
  dashboardMap.landmarks.forEach((landmark) => {
    points.set(landmark.id, { x: landmark.x, z: landmark.z });
  });
  return points;
}

function mapCenter(dashboardMap: DashboardMap) {
  const coordinates = [
    ...dashboardMap.buildings.map((building) => ({ x: building.x, z: building.z })),
    ...dashboardMap.landmarks.map((landmark) => ({ x: landmark.x, z: landmark.z })),
    ...dashboardMap.districts.map((district) => ({ x: district.x, z: district.z }))
  ];
  if (!coordinates.length) {
    return { x: 0, z: 0, width: 48, depth: 36 };
  }
  const minX = Math.min(...coordinates.map((item) => item.x));
  const maxX = Math.max(...coordinates.map((item) => item.x));
  const minZ = Math.min(...coordinates.map((item) => item.z));
  const maxZ = Math.max(...coordinates.map((item) => item.z));
  return {
    x: (minX + maxX) / 2,
    z: (minZ + maxZ) / 2,
    width: Math.max(12, maxX - minX),
    depth: Math.max(12, maxZ - minZ)
  };
}

function useReducedMotion() {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const handleChange = () => setReduced(query.matches);
    handleChange();
    query.addEventListener("change", handleChange);
    return () => query.removeEventListener("change", handleChange);
  }, []);

  return reduced;
}

function riskTone(value?: string) {
  if (!value) return "neutral";
  const clean = value.toLowerCase();
  if (["fresh", "healthy", "low", "done", "present", "ok", "good", "completed"].includes(clean)) return "good";
  if (["stale", "warning", "warn", "medium", "unknown", "blocked"].includes(clean)) return "warn";
  if (["high", "risk", "missing", "failed", "error", "invalid"].includes(clean)) return "risk";
  if (["memory", "selected"].includes(clean)) return "memory";
  return clean;
}
