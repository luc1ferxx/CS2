// The stage's one graticule: a faint plotting grid drawn over the radar image and under every mark,
// 25 squares a side (about 24 px on a desktop stage). The radar PNGs carry their own baked background,
// so a grid behind the image would only show in the margins around it.
const STEP = 4;
const GRATICULE_PATH = Array.from({ length: 100 / STEP - 1 }, (_, index) => {
  const at = (index + 1) * STEP;
  return `M0 ${at}H100M${at} 0V100`;
}).join("");

export function RadarGraticule() {
  return <path className="map-graticule" d={GRATICULE_PATH} vectorEffect="non-scaling-stroke" aria-hidden="true" />;
}
