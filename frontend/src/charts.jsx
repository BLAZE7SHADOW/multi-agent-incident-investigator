import { useState } from "react";

export const SERIES = {
  multi_agent: { label: "Multi-agent", color: "var(--series-1)" },
  baseline: { label: "Single agent", color: "var(--series-2)" },
};

function Tooltip({ tip }) {
  if (!tip) return null;
  return (
    <div className="tooltip" style={{ left: tip.x, top: tip.y }} role="status">
      {tip.rows.map((row) => (
        <div key={row.label} className="tooltip-row">
          {row.color && <span className="key-line" style={{ background: row.color }} />}
          <strong>{row.value}</strong>
          <span className="muted">{row.label}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ systems }) {
  return (
    <div className="legend">
      {systems.map((s) => (
        <span key={s} className="legend-item">
          <span className="key-box" style={{ background: SERIES[s].color }} />
          {SERIES[s].label}
        </span>
      ))}
    </div>
  );
}

// Horizontal bars, one group per metric, one bar per system. HTML, so text stays readable at
// any width. Values are labelled directly; the tooltip repeats them on hover and focus.
export function CompareBars({ title, rows, systems, format, max }) {
  const [tip, setTip] = useState(null);
  const top = max ?? Math.max(...rows.flatMap((r) => systems.map((s) => r.values[s] ?? 0)), 1);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <Legend systems={systems} />
      <div className="chart-frame bars" onPointerLeave={() => setTip(null)}>
        {rows.map((row) => (
          <div key={row.label} className="bar-group">
            {rows.length > 1 && <div className="bar-label">{row.label}</div>}
            {systems.map((s) => {
              const value = row.values[s] ?? 0;
              const text = format(value);
              const show = (e) => {
                const frame = e.currentTarget.closest(".chart-frame").getBoundingClientRect();
                const mark = e.currentTarget.getBoundingClientRect();
                setTip({
                  x: (e.clientX ?? mark.right) - frame.left + 12,
                  y: (e.clientY ?? mark.top) - frame.top - 4,
                  rows: [{ label: `${SERIES[s].label}, ${row.label}`, value: text, color: SERIES[s].color }],
                });
              };
              return (
                <div
                  key={s}
                  className="bar-row"
                  tabIndex={0}
                  aria-label={`${SERIES[s].label}, ${row.label}: ${text}`}
                  onPointerMove={show}
                  onFocus={show}
                  onBlur={() => setTip(null)}
                >
                  <div className="bar-track">
                    <div
                      className="bar"
                      style={{ width: `${Math.max(0.5, (value / top) * 100)}%`, background: SERIES[s].color }}
                    />
                  </div>
                  <span className="value-label">{text}</span>
                </div>
              );
            })}
          </div>
        ))}
        <Tooltip tip={tip} />
      </div>
    </figure>
  );
}

// One metric series over time, with a crosshair that snaps to the nearest point.
export function MetricLine({ series, isKey = false }) {
  const [hover, setHover] = useState(null);
  const width = 320;
  const height = 120;
  const pad = { left: 44, right: 10, top: 10, bottom: 22 };
  const values = series.values;
  const min = Math.min(...values, 0);
  const max = Math.max(...values) || 1;
  const x = (i) => pad.left + (i / Math.max(values.length - 1, 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - (v - min) / (max - min || 1)) * (height - pad.top - pad.bottom);
  // Timestamps carry no timezone, so read and print them as UTC to avoid any local shift.
  const start = Date.parse(`${series.start}:00Z`);
  const stamp = (i) => new Date(start + i * series.step_min * 60000).toISOString().slice(11, 16);
  const path = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const pick = (e) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    const i = Math.round(((px - pad.left) / (width - pad.left - pad.right)) * (values.length - 1));
    setHover(Math.min(values.length - 1, Math.max(0, i)));
  };
  const label = `${series.service} ${series.metric}${series.region ? ` (${series.region})` : ""}`;
  return (
    <figure className="chart small">
      <figcaption>
        {label} <span className="muted">· {series.id}{isKey ? " ★" : ""}</span>
      </figcaption>
      <div className="chart-frame">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={label}
          onPointerMove={pick}
          onPointerLeave={() => setHover(null)}
        >
          <line x1={pad.left} x2={width - pad.right} y1={y(min)} y2={y(min)} className="baseline" />
          <text x={pad.left - 6} y={y(max) + 4} className="axis-label" textAnchor="end">
            {max}
          </text>
          <text x={pad.left - 6} y={y(min) + 4} className="axis-label" textAnchor="end">
            {min}
          </text>
          <text x={pad.left} y={height - 6} className="axis-label">
            {stamp(0)}
          </text>
          <text x={width - pad.right} y={height - 6} className="axis-label" textAnchor="end">
            {stamp(values.length - 1)}
          </text>
          <path d={path} fill="none" stroke="var(--series-1)" strokeWidth={2} />
          {hover !== null && (
            <>
              <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={height - pad.bottom} className="crosshair" />
              <circle cx={x(hover)} cy={y(values[hover])} r={4} fill="var(--series-1)" stroke="var(--surface-1)" strokeWidth={2} />
            </>
          )}
        </svg>
        {hover !== null && (
          <Tooltip
            tip={{
              x: `${(x(hover) / width) * 100}%`,
              y: 0,
              rows: [{ label: stamp(hover), value: String(values[hover]), color: "var(--series-1)" }],
            }}
          />
        )}
      </div>
    </figure>
  );
}
