"use client";

/**
 * Day-aligned line chart, drawn as inline SVG.
 *
 * Deliberately dependency-free and deliberately plain: the x-axis is day index, not calendar
 * time, which is the whole point — a 2019 campaign and a live one occupy the same axis.
 */

export interface Line {
  label: string;
  color: string;
  points: { x: number; y: number }[];
}

const PALETTE = ["#1d4ed8", "#b45309", "#15803d", "#7c3aed"];

export function seriesColor(index: number): string {
  return PALETTE[index % PALETTE.length];
}

export function LineChart({
  lines,
  yLabel,
  height = 220,
  width = 720,
}: {
  lines: Line[];
  yLabel: string;
  height?: number;
  width?: number;
}) {
  const all = lines.flatMap((l) => l.points);
  if (!all.length) {
    return (
      <div className="empty-state">
        <strong>Nothing to plot.</strong>
        No dated coverage has been imported for the selected campaigns.
      </div>
    );
  }

  const pad = { top: 12, right: 16, bottom: 30, left: 48 };
  const xs = all.map((p) => p.x);
  const ys = all.map((p) => p.y);
  const xMin = Math.min(...xs);
  const xMax = Math.max(...xs, xMin + 1);
  const yMax = Math.max(...ys, 1);

  const sx = (x: number) =>
    pad.left + ((x - xMin) / (xMax - xMin)) * (width - pad.left - pad.right);
  const sy = (y: number) =>
    height - pad.bottom - (y / yMax) * (height - pad.top - pad.bottom);

  const yTicks = niceTicks(yMax, 4);
  const xTicks = niceTicks(xMax - xMin, 6).map((t) => Math.round(xMin + t));

  return (
    <div>
      <svg
        className="chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${yLabel} by day index`}
      >
        {yTicks.map((t) => (
          <g key={`y${t}`}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={sy(t)}
              y2={sy(t)}
              stroke="#e3e7ec"
            />
            <text x={pad.left - 6} y={sy(t) + 4} fontSize="12" fill="#5b6672" textAnchor="end">
              {t.toLocaleString()}
            </text>
          </g>
        ))}
        {xTicks.map((t) => (
          <text
            key={`x${t}`}
            x={sx(t)}
            y={height - pad.bottom + 14}
            fontSize="12"
            fill="#5b6672"
            textAnchor="middle"
          >
            {t}
          </text>
        ))}
        {/* Day 0 is publication. Marked, because negative day indices are real coverage. */}
        {xMin < 0 && xMax > 0 && (
          <line
            x1={sx(0)}
            x2={sx(0)}
            y1={pad.top}
            y2={height - pad.bottom}
            stroke="#b8c1cc"
            strokeDasharray="3 3"
          />
        )}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={height - pad.bottom}
          y2={height - pad.bottom}
          stroke="#b8c1cc"
        />
        {lines.map((line) => (
          <polyline
            key={line.label}
            fill="none"
            stroke={line.color}
            strokeWidth="2"
            strokeLinejoin="round"
            points={line.points.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")}
          />
        ))}
        <text
          x={10}
          y={pad.top + 2}
          fontSize="12"
          fill="#5b6672"
          transform={`rotate(-90 10 ${pad.top + 2})`}
          textAnchor="end"
        >
          {yLabel}
        </text>
        <text
          x={width / 2}
          y={height - 2}
          fontSize="12"
          fill="#5b6672"
          textAnchor="middle"
        >
          Day index (0 = publication)
        </text>
      </svg>
      <div className="legend">
        {lines.map((l) => (
          <span key={l.label}>
            <i className="swatch" style={{ background: l.color }} />
            {l.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function niceTicks(max: number, count: number): number[] {
  if (max <= 0) return [0];
  const raw = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? magnitude * 10;
  const ticks: number[] = [];
  for (let t = 0; t <= max + step / 2; t += step) ticks.push(Math.round(t * 100) / 100);
  return ticks;
}
