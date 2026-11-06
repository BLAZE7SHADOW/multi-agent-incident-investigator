import { Alert, Button, Card, Col, Descriptions, Row, Segmented, Select, Space, Statistic, Table, Tag, Typography } from "antd";
import { useMemo, useState } from "react";
import { CompareBars, MetricLine, SERIES } from "./charts.jsx";

const { Text, Paragraph } = Typography;
const SYSTEMS = ["multi_agent", "baseline"];
const pct = (v) => (v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`);
const num = (v) => Math.round(v).toLocaleString();

function Status({ run }) {
  if (!run.valid) return <Tag>invalid</Tag>;
  if (run.correct) return <Tag color="green">✓ right</Tag>;
  if (run.wrong_conclusion) return <Tag color="red">✕ wrong conclusion</Tag>;
  return <Tag color="orange">○ missed</Tag>;
}

export function Results({ data }) {
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="Same model, tools, budget and evidence rules for both systems. The only difference is one agent doing all the work. Scored by plain code, with no LLM judge."
      />
      <Row gutter={[16, 16]}>
        {data.evaluations.map((e) => {
          const s = e.summary;
          const rate = ([label, key]) => ({ label, values: Object.fromEntries(SYSTEMS.map((x) => [x, s[x][key]])) });
          const runs = {};
          for (const run of e.runs) (runs[run.incident_id] ??= { id: run.incident_id, multi_agent: [], baseline: [] })[run.system].push(run);
          return (
            <Col key={e.id} xs={24} xxl={12}>
              <Card title={e.title} extra={<Text type="secondary">{e.detail}</Text>}>
                <Row gutter={16}>
                  {SYSTEMS.map((x) => (
                    <Col key={x} span={12}>
                      <Statistic title={`${SERIES[x].label} accuracy`} value={Math.round(s[x].accuracy * 100)} suffix="%" />
                    </Col>
                  ))}
                </Row>
                <Row gutter={[24, 16]} style={{ marginTop: 16 }}>
                  <Col xs={24} lg={14}>
                    <CompareBars
                      title="Outcomes, share of runs"
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
                  </Col>
                  <Col xs={24} lg={10}>
                    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                      <CompareBars title="Tokens per run" systems={SYSTEMS} format={num} rows={[rate(["Tokens", "total_tokens"])]} />
                      <CompareBars title="Mean latency" systems={SYSTEMS} format={(v) => `${Math.round(v)}s`} rows={[rate(["Latency", "latency_s"])]} />
                    </Space>
                  </Col>
                </Row>
                <Table
                  style={{ marginTop: 16 }}
                  size="small"
                  rowKey="id"
                  pagination={{ pageSize: 6, size: "small" }}
                  dataSource={Object.values(runs).sort((a, b) => a.id.localeCompare(b.id))}
                  columns={[
                    { title: "Incident", dataIndex: "id", width: 100 },
                    ...SYSTEMS.map((x) => ({
                      title: SERIES[x].label,
                      key: x,
                      render: (row) => (
                        <Space size={4} wrap>
                          {row[x].map((r) => (
                            <Status key={r.repeat} run={r} />
                          ))}
                        </Space>
                      ),
                    })),
                  ]}
                />
              </Card>
            </Col>
          );
        })}
      </Row>
    </Space>
  );
}

export function Data({ data }) {
  const [set, setSet] = useState("assignment");
  const cases = data.incidents.filter((c) => c.set === set);
  const [id, setId] = useState(cases[0].incident.id);
  const [reveal, setReveal] = useState(false);
  const current = cases.find((c) => c.incident.id === id) ?? cases[0];
  const { truth, world } = current;
  const keyRows = new Set(reveal ? truth.key_evidence : []);
  const mark = (rowId) => (keyRows.has(rowId) ? `${rowId} ★` : rowId);
  const table = (title, rows, columns) =>
    rows?.length ? (
      <Card size="small" title={title}>
        <Table
          size="small"
          rowKey="id"
          pagination={false}
          dataSource={rows}
          rowClassName={(r) => (keyRows.has(r.id) ? "key-row" : "")}
          columns={columns.map((c) => ({ title: c, dataIndex: c, render: c === "id" ? mark : (v) => String(v ?? "") }))}
        />
      </Card>
    ) : null;
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Space wrap>
          <Segmented
            value={set}
            onChange={(v) => {
              setSet(v);
              setId(data.incidents.find((c) => c.set === v).incident.id);
              setReveal(false);
            }}
            options={[
              { value: "assignment", label: "Assignment set (12)" },
              { value: "blind", label: "Blind set (24)" },
            ]}
          />
          <Select
            style={{ minWidth: 360 }}
            value={current.incident.id}
            onChange={(v) => {
              setId(v);
              setReveal(false);
            }}
            options={cases.map((c) => ({ value: c.incident.id, label: `${c.incident.id}: ${c.incident.title}` }))}
          />
          <Button onClick={() => setReveal(!reveal)}>{reveal ? "Hide answer" : "Reveal answer"}</Button>
        </Space>
      </Card>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={10}>
          <Card size="small" title={`${current.incident.id}: ${current.incident.title}`}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Report">{current.incident.description}</Descriptions.Item>
              <Descriptions.Item label="Service">{current.incident.service}</Descriptions.Item>
              <Descriptions.Item label="Time">{current.incident.reported_at}</Descriptions.Item>
              <Descriptions.Item label="Difficulty">
                {(current.tags ?? []).map((t) => (
                  <Tag key={t}>{t}</Tag>
                ))}
              </Descriptions.Item>
              {world.faults?.length > 0 && (
                <Descriptions.Item label="Tool failures">
                  {world.faults.map((f) => `${f.tool} ${f.kind}${f.times ? ` ×${f.times}` : ", always"}`).join("; ")}
                </Descriptions.Item>
              )}
            </Descriptions>
            {reveal && (
              <Alert
                style={{ marginTop: 8 }}
                type="info"
                message={truth.outcome === "conclusive" ? `${truth.category} in ${truth.component}` : "Inconclusive"}
                description={current.scenario}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={14}>
          {world.metrics?.length > 0 && (
            <Card size="small" title="Metrics">
              <div className="multiples">
                {world.metrics.map((m) => (
                  <MetricLine key={m.id} series={m} isKey={keyRows.has(m.id)} />
                ))}
              </div>
            </Card>
          )}
        </Col>
      </Row>
      {table("Logs", world.logs, ["id", "ts", "service", "level", "msg"])}
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>{table("Deployments and changes", world.deployments, ["id", "ts", "service", "kind", "detail"])}</Col>
        <Col xs={24} xl={10}>{table("Past incidents", world.history, ["id", "summary"])}</Col>
      </Row>
    </Space>
  );
}

export function Checks({ data }) {
  const rules = data.rule_eval;
  const transforms = useMemo(() => {
    const names = [...new Set(data.metamorphic.assignment.map((r) => r.transform))];
    const count = (rows, name) => {
      const g = rows.filter((r) => r.transform === name);
      return `${g.filter((r) => r.passed).length}/${g.length}`;
    };
    return names.map((name) => ({ name, assignment: count(data.metamorphic.assignment, name), blind: count(data.metamorphic.blind, name) }));
  }, [data]);
  const sets = ["reconstructed", "control", "adversarial"].map((name) => {
    const g = rules.scenarios.filter((s) => s.set === name);
    return { name, result: `${g.filter((s) => s.correct).length}/${g.length}` };
  });
  const coverage = (counts) => Object.entries(counts).map(([name, cases]) => ({ name, cases }));
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon message="These checks run the real tools and evidence rules, with labels a perfect Verifier would give. No model calls." />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card size="small" title="Evidence rule evaluation">
            <Paragraph type="secondary">
              Precision {rules.confusion.tp}/{rules.confusion.tp + rules.confusion.fp}, recall {rules.confusion.tp}/{rules.confusion.tp + rules.confusion.fn}.
            </Paragraph>
            <Table size="small" rowKey="name" pagination={false} dataSource={sets} columns={[{ title: "Set", dataIndex: "name" }, { title: "Correct", dataIndex: "result" }]} />
          </Card>
        </Col>
        <Col xs={24} xl={16}>
          <Card size="small" title="Metamorphic tests: changes that must not change the answer">
            <Table
              size="small"
              rowKey="name"
              pagination={false}
              dataSource={transforms}
              columns={[
                { title: "Change", dataIndex: "name" },
                { title: "Assignment set", dataIndex: "assignment", width: 140 },
                { title: "Blind set", dataIndex: "blind", width: 120 },
              ]}
            />
          </Card>
        </Col>
      </Row>
      <Row gutter={[16, 16]}>
        {[
          ["Coverage: root-cause category", data.coverage.categories],
          ["Coverage: difficulty type", data.coverage.tags],
        ].map(([title, counts]) => (
          <Col key={title} xs={24} lg={12}>
            <Card size="small" title={title}>
              <Table
                size="small"
                rowKey="name"
                pagination={false}
                dataSource={coverage(counts)}
                columns={[
                  { title: "Type", dataIndex: "name" },
                  { title: "Cases", dataIndex: "cases", width: 90, render: (n) => (n < 3 ? <Tag color="red">{n}</Tag> : n) },
                ]}
              />
            </Card>
          </Col>
        ))}
      </Row>
    </Space>
  );
}

const REQUIREMENTS = [
  ["Multi-agent architecture", "7 agents: Orchestrator, Observability, Dependency, Reasoning, Verifier, Critic, Reporting", "Investigation replay: each step names its agent"],
  ["Agents exchange structured results", "Typed plans, findings, hypotheses, labels and critiques", "Plan, evidence, hypotheses and verify panels"],
  ["Tools with structured I/O and simulated failures", "Logs, metrics, deployments, dependencies, past incidents; timeouts, errors, partial results", "Evidence panel: every tool call with its query, status and rows"],
  ["Parallel tasks and dependencies", "Independent tasks run in parallel; a task can wait for another", "Plan panel: 'Waits for' column"],
  ["Feedback loops", "The Critic, or a tie between causes, triggers another round", "INC-08 replay: round 2"],
  ["Bounded retries, limits, recovery", "Retries, run and task limits, fallbacks, checkpoints", "INC-08 replay: the deploy tool fails and is retried"],
  ["Ranked hypotheses with evidence and confidence", "Code computes confidence from Verifier labels", "Report panel"],
  ["Facts vs assumptions, inconclusive outcome", "Unsupported facts become assumptions; weak evidence ends inconclusive", "Report panel; INC-08 ends inconclusive"],
  ["10+ test incidents with misleading data and failures", "12 assignment incidents and 24 blind incidents", "Test incidents tab"],
  ["Comparison with a single-agent baseline", "Accuracy, grounding, tool calls, latency, tokens", "Results tab"],
];

export function Requirements() {
  return (
    <Card size="small" title="How this system meets the assignment">
      <Table
        size="small"
        rowKey={(r) => r[0]}
        pagination={false}
        dataSource={REQUIREMENTS}
        columns={[
          { title: "Requirement", render: (r) => <Text strong>{r[0]}</Text>, width: "28%" },
          { title: "How", render: (r) => r[1] },
          { title: "Where to see it", render: (r) => <Text type="secondary">{r[2]}</Text>, width: "30%" },
        ]}
      />
    </Card>
  );
}
