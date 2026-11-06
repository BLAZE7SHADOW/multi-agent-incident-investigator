import { useEffect, useMemo, useState } from "react";
import { CompareBars, MetricLine, SERIES } from "./charts.jsx";

const SYSTEMS = ["multi_agent", "baseline"];
const pct = (v) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`);
const num = (v) => Math.round(v).toLocaleString();
const secs = (v) => `${Math.round(v)}s`;

function Status({ run }) {
  if (!run.valid) return <span className="status muted">◌ invalid</span>;
  if (run.correct) return <span className="status good">✓ right</span>;
  if (run.wrong_conclusion) return <span className="status critical">✕ wrong conclusion</span>;
  return <span className="status serious">○ missed</span>;
}

function Overview({ data }) {
  return (
    <div className="stack">
      <p className="lede">
        Both systems use the same model, tools, budget and evidence rules. The only difference is one
        agent doing all the work. Scoring is plain code, with no LLM judge.
      </p>
      {data.evaluations.map((e) => {
        const s = e.summary;
        const rate = (key) => ({ label: key[0], values: Object.fromEntries(SYSTEMS.map((x) => [x, s[x][key[1]]])) });
        const byIncident = {};
        for (const run of e.runs) {
          const row = (byIncident[run.incident_id] ??= { multi_agent: [], baseline: [] });
          row[run.system].push(run);
        }
        return (
          <section key={e.id} className="card">
            <h2>{e.title}</h2>
            <p className="muted">{e.detail}</p>
            <div className="tiles">
              {SYSTEMS.map((x) => (
                <div key={x} className="tile">
                  <span className="muted">{SERIES[x].label} accuracy</span>
                  <strong className="hero">{pct(s[x].accuracy)}</strong>
                  <span className="muted">{s[x].runs} valid runs</span>
                </div>
              ))}
            </div>
            <div className="grid-2">
              <CompareBars
                title="Outcomes (share of runs)"
                systems={SYSTEMS}
                format={pct}
                max={1}
                rows={[
                  ["Root-cause accuracy", "accuracy"],
                  ["Wrong conclusions", "wrong_conclusion_rate"],
                  ["Missed conclusions", "missed_conclusion_rate"],
                  ["Key evidence recall", "evidence_recall"],
                ].map(rate)}
              />
              <div className="stack">
                <CompareBars title="Tokens per run" systems={SYSTEMS} format={num} rows={[rate(["Tokens", "total_tokens"])]} />
                <CompareBars title="Mean latency" systems={SYSTEMS} format={secs} rows={[rate(["Latency", "latency_s"])]} />
                <CompareBars title="LLM calls per run" systems={SYSTEMS} format={(v) => v.toFixed(1)} rows={[rate(["LLM calls", "llm_calls"])]} />
              </div>
            </div>
            <details>
              <summary>Every incident, every run</summary>
              <table>
                <thead>
                  <tr>
                    <th>Incident</th>
                    {SYSTEMS.map((x) => (
                      <th key={x}>{SERIES[x].label}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(byIncident).sort().map(([id, row]) => (
                    <tr key={id}>
                      <td>{id}</td>
                      {SYSTEMS.map((x) => (
                        <td key={x}>
                          {row[x].map((run) => (
                            <div key={run.repeat}>
                              <Status run={run} />{" "}
                              <span className="muted">
                                {run.top_category ? `${run.top_category}/${run.top_component}` : "no hypothesis"}
                              </span>
                            </div>
                          ))}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          </section>
        );
      })}
    </div>
  );
}

function Rows({ title, rows, columns, keyRows }) {
  if (!rows?.length) return null;
  return (
    <section>
      <h3>{title}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} className={keyRows.has(row.id) ? "key-row" : ""}>
                {columns.map((c) => (
                  <td key={c}>{c === "id" && keyRows.has(row.id) ? `${row.id} ★` : String(row[c] ?? "")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Incidents({ data }) {
  const [set, setSet] = useState("assignment");
  const cases = data.incidents.filter((c) => c.set === set);
  const [id, setId] = useState(cases[0].incident.id);
  const [reveal, setReveal] = useState(false);
  const current = data.incidents.find((c) => c.incident.id === id) ?? cases[0];
  const truth = current.truth;
  const world = current.world;
  const keyRows = new Set(reveal ? truth.key_evidence : []);
  const runs = data.evaluations.flatMap((e) =>
    e.runs.filter((r) => r.incident_id === current.incident.id).map((r) => ({ ...r, eval: e.id })),
  );
  const choose = (next) => {
    setId(next);
    setReveal(false);
  };
  return (
    <div className="incidents">
      <div className="filters">
        {["assignment", "blind"].map((name) => (
          <button
            key={name}
            className={name === set ? "chip active" : "chip"}
            onClick={() => {
              setSet(name);
              choose(data.incidents.find((c) => c.set === name).incident.id);
            }}
          >
            {name === "assignment" ? "Assignment (12)" : "Blind (24)"}
          </button>
        ))}
        <select value={current.incident.id} onChange={(e) => choose(e.target.value)} aria-label="Incident">
          {cases.map((c) => (
            <option key={c.incident.id} value={c.incident.id}>
              {c.incident.id}: {c.incident.title}
            </option>
          ))}
        </select>
      </div>
      <section className="card">
        <h2>
          {current.incident.id}: {current.incident.title}
        </h2>
        <p>{current.incident.description}</p>
        <p className="muted">
          Reported on <strong>{current.incident.service}</strong> at {current.incident.reported_at}
          {current.tags?.length ? ` · ${current.tags.join(", ")}` : ""}
        </p>
        <button className="chip" onClick={() => setReveal(!reveal)}>
          {reveal ? "Hide the answer" : "Reveal the answer"}
        </button>
        {reveal && (
          <div className="answer">
            <strong>
              {truth.outcome === "conclusive" ? `${truth.category} in ${truth.component}` : "Inconclusive: the evidence cannot establish a cause"}
            </strong>
            <span className="muted">{current.scenario}</span>
            {truth.key_evidence?.length > 0 && <span className="muted">Key evidence, marked ★ below: {truth.key_evidence.join(", ")}</span>}
          </div>
        )}
      </section>

      {runs.length > 0 && (
        <section className="card">
          <h3>What each system concluded</h3>
          <table>
            <thead>
              <tr>
                <th>Evaluation</th>
                <th>System</th>
                <th>Result</th>
                <th>Top cause</th>
                <th>Confidence</th>
                <th>Tokens</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={`${r.eval}-${r.system}-${r.repeat}`}>
                  <td>{r.eval}</td>
                  <td>{SERIES[r.system].label}</td>
                  <td>{reveal ? <Status run={r} /> : <span className="muted">hidden</span>}</td>
                  <td>{r.top_category ? `${r.top_category}/${r.top_component}` : "none"}</td>
                  <td>{r.top_confidence}</td>
                  <td>{num(r.total_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {world.metrics?.length > 0 && (
        <section className="card">
          <h3>Metrics</h3>
          <div className="multiples">
            {world.metrics.map((m) => (
              <MetricLine key={m.id} series={m} isKey={keyRows.has(m.id)} />
            ))}
          </div>
        </section>
      )}
      <section className="card">
        <Rows title="Logs" rows={world.logs} columns={["id", "ts", "service", "level", "msg"]} keyRows={keyRows} />
        <Rows title="Deployments and changes" rows={world.deployments} columns={["id", "ts", "service", "kind", "detail"]} keyRows={keyRows} />
        <Rows title="Past incidents" rows={world.history} columns={["id", "summary"]} keyRows={keyRows} />
        {world.faults?.length > 0 && (
          <p className="muted">
            Simulated tool failures:{" "}
            {world.faults.map((f) => `${f.tool} ${f.kind}${f.times ? ` ×${f.times}` : ", always"}`).join("; ")}
          </p>
        )}
      </section>
    </div>
  );
}

function Checks({ data }) {
  const rules = data.rule_eval;
  const sets = ["reconstructed", "control", "adversarial"];
  const transforms = useMemo(() => {
    const names = [...new Set(data.metamorphic.assignment.map((r) => r.transform))];
    return names.map((name) => {
      const count = (rows) => {
        const group = rows.filter((r) => r.transform === name);
        return `${group.filter((r) => r.passed).length}/${group.length}`;
      };
      return { name, assignment: count(data.metamorphic.assignment), blind: count(data.metamorphic.blind) };
    });
  }, [data]);
  return (
    <div className="stack">
      <p className="lede">These checks run the real tools and evidence rules with no model calls.</p>
      <section className="card">
        <h2>Evidence rule evaluation</h2>
        <p className="muted">
          Labels a perfect Verifier would give. Precision {rules.confusion.tp}/{rules.confusion.tp + rules.confusion.fp},
          recall {rules.confusion.tp}/{rules.confusion.tp + rules.confusion.fn}.
        </p>
        <table>
          <thead>
            <tr>
              <th>Set</th>
              <th>Correct</th>
            </tr>
          </thead>
          <tbody>
            {sets.map((name) => {
              const group = rules.scenarios.filter((s) => s.set === name);
              return (
                <tr key={name}>
                  <td>{name}</td>
                  <td>
                    {group.filter((s) => s.correct).length}/{group.length}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Metamorphic tests</h2>
        <p className="muted">Each change should not change the answer, or should not raise confidence.</p>
        <table>
          <thead>
            <tr>
              <th>Change</th>
              <th>Assignment</th>
              <th>Blind</th>
            </tr>
          </thead>
          <tbody>
            {transforms.map((t) => (
              <tr key={t.name}>
                <td>{t.name}</td>
                <td>{t.assignment}</td>
                <td>{t.blind}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <h2>Coverage, both test sets</h2>
        <div className="grid-2">
          {[
            ["Root-cause category", data.coverage.categories],
            ["Difficulty type", data.coverage.tags],
          ].map(([title, counts]) => (
            <table key={title}>
              <thead>
                <tr>
                  <th>{title}</th>
                  <th>Cases</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(counts).map(([name, n]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td className={n < 3 ? "low" : ""}>{n}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ))}
        </div>
      </section>
    </div>
  );
}

const PAGES = { overview: ["Overview", Overview], incidents: ["Incidents", Incidents], checks: ["Offline checks", Checks] };

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(() => location.hash.slice(1) || "overview");
  useEffect(() => {
    fetch("data.json")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    location.hash = page;
  }, [page]);
  const Page = (PAGES[page] ?? PAGES.overview)[1];
  return (
    <div className="app">
      <header>
        <h1>Incident Investigator</h1>
        <nav>
          {Object.entries(PAGES).map(([key, [label]]) => (
            <button key={key} className={key === page ? "tab active" : "tab"} onClick={() => setPage(key)}>
              {label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {error && <p className="status critical">Could not load data.json: {error}</p>}
        {!data && !error && <p className="muted">Loading…</p>}
        {data && <Page data={data} />}
      </main>
    </div>
  );
}
