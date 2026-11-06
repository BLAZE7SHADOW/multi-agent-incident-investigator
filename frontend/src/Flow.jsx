import {
  CaretRightOutlined,
  PauseOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
  UndoOutlined,
} from "@ant-design/icons";
import { Alert, Button, Card, Col, Descriptions, Empty, Row, Segmented, Slider, Space, Steps, Table, Tag, Timeline, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";

const { Text, Paragraph } = Typography;

const STAGES = [
  { title: "Incident", description: "Report arrives" },
  { title: "Plan", description: "Orchestrator" },
  { title: "Gather evidence", description: "Specialists, in parallel" },
  { title: "Hypotheses", description: "Reasoning" },
  { title: "Verify", description: "Verifier and evidence rules" },
  { title: "Critique", description: "Critic" },
  { title: "Report", description: "Outcome and actions" },
];
const STAGE_OF = { orchestrate: 1, run_task: 2, reason: 3, verify: 4, criticize: 5, finalize: 6 };
const AGENT_LABEL = {
  orchestrate: "Orchestrator",
  run_task: "Specialist agent",
  reason: "Reasoning agent",
  verify: "Verifier agent",
  criticize: "Critic agent",
  finalize: "Reporting agent and outcome rules",
};
const LABEL_COLOR = { supports: "green", contradicts: "red", symptom: "gold", irrelevant: "default" };
const STATUS_COLOR = { ok: "green", partial: "gold", timeout: "red", error: "red", skipped: "default", rejected: "default" };

// Turn the recorded events into replayable phases: one per workflow step, each with its tool calls.
function buildPhases(trace) {
  const phases = [{ stage: 0, node: "incident", round: 0, t: 0 }];
  const tools = trace.events.filter((e) => e.type === "tool");
  const used = new Set();
  let round = 0;
  for (const event of trace.events) {
    if (event.type !== "step" || !(event.node in STAGE_OF)) continue;
    if (event.node === "orchestrate") round += 1;
    const phase = { stage: STAGE_OF[event.node], node: event.node, round, t: event.t, update: event.update };
    if (event.node === "run_task") {
      const task = Object.values(event.update.tasks ?? {})[0];
      phase.task = task;
      phase.tools = tools.filter((x, i) => !used.has(i) && x.agent === task?.agent && x.t <= event.t && (used.add(i) || true));
    }
    phases.push(phase);
  }
  return phases;
}

function phaseTitle(p) {
  if (p.node === "incident") return "Incident report received";
  if (p.node === "orchestrate") return `Round ${p.round}: plan ${Object.keys(p.update.tasks ?? {}).length} tasks`;
  if (p.node === "run_task") return `${p.task?.agent === "observability" ? "Observability" : "Dependency"} agent: ${p.task?.status === "failed" ? "task failed" : `${Object.keys(p.update.evidence ?? {}).length} rows found`}`;
  if (p.node === "reason") return `Reasoning: ${p.update.hypotheses?.length ?? 0} competing causes`;
  if (p.node === "verify") return "Verifier labels the evidence";
  if (p.node === "criticize") return `Critic: ${p.update.critique?.verdict ?? "failed"}`;
  return `Report: ${p.update.report?.outcome}`;
}

function IncidentPanel({ trace }) {
  const i = trace.incident;
  return (
    <Descriptions column={{ xs: 1, md: 2 }} size="small" bordered>
      <Descriptions.Item label="Incident" span={2}>
        <Text strong>{i.title}</Text>
      </Descriptions.Item>
      <Descriptions.Item label="Description" span={2}>{i.description}</Descriptions.Item>
      <Descriptions.Item label="Reported on">{i.service}</Descriptions.Item>
      <Descriptions.Item label="Time">{i.reported_at}</Descriptions.Item>
      <Descriptions.Item label="Data available" span={2}>
        {trace.world.logs.length} logs, {trace.world.metrics.length} metric series, {trace.world.deployments.length} changes,{" "}
        {trace.world.history.length} past incidents
        {trace.world.faults.length > 0 && (
          <> · <Tag color="red">{trace.world.faults.length} simulated tool failure(s)</Tag></>
        )}
      </Descriptions.Item>
    </Descriptions>
  );
}

function PlanPanel({ phase }) {
  const tasks = Object.values(phase.update.tasks ?? {});
  return (
    <Table
      size="small"
      pagination={false}
      rowKey="id"
      dataSource={tasks}
      columns={[
        { title: "Task", dataIndex: "id", width: 140 },
        { title: "Agent", dataIndex: "agent", width: 130, render: (a) => <Tag color={a === "observability" ? "blue" : "purple"}>{a}</Tag> },
        { title: "Question", dataIndex: "question" },
        { title: "Waits for", dataIndex: "depends_on", width: 120, render: (d) => (d.length ? d.join(", ") : "nothing, runs in parallel") },
      ]}
    />
  );
}

function EvidencePanel({ phase }) {
  const finding = phase.task?.finding;
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Text type="secondary">Question: {phase.task?.question}</Text>
      <Table
        size="small"
        pagination={false}
        rowKey={(r, i) => `${r.t}-${i}`}
        dataSource={phase.tools}
        locale={{ emptyText: "No tool calls" }}
        columns={[
          { title: "At", dataIndex: "t", width: 70, render: (t) => `${t}s` },
          { title: "Tool", dataIndex: "tool", width: 150, render: (t) => <Text code>{t}</Text> },
          { title: "Query", dataIndex: "args", render: (a) => <Text type="secondary">{JSON.stringify(a)}</Text> },
          { title: "Result", dataIndex: "status", width: 190, render: (s) => <Tag color={STATUS_COLOR[s] ?? "red"}>{s}</Tag> },
          { title: "Rows returned", dataIndex: "rows", render: (r) => (r.length ? r.join(", ") : "none") },
        ]}
      />
      {finding && (
        <Card size="small" title="Finding">
          <Paragraph>{finding.summary}</Paragraph>
          {finding.claims.map((c, i) => (
            <div key={i} className="claim">
              <Tag color={c.kind === "fact" ? "green" : c.kind === "inference" ? "blue" : "default"}>{c.kind}</Tag>
              {c.text} {c.evidence_ids.length > 0 && <Text type="secondary">[{c.evidence_ids.join(", ")}]</Text>}
            </div>
          ))}
          {finding.gaps.length > 0 && <Alert type="warning" showIcon style={{ marginTop: 8 }} message={`Gaps: ${finding.gaps.join("; ")}`} />}
        </Card>
      )}
    </Space>
  );
}

function HypothesesPanel({ phase }) {
  return (
    <Table
      size="small"
      pagination={false}
      rowKey="id"
      dataSource={phase.update.hypotheses ?? []}
      columns={[
        { title: "Cause", dataIndex: "statement" },
        { title: "Category", dataIndex: "category", width: 160, render: (c) => <Tag>{c}</Tag> },
        { title: "Component", dataIndex: "component", width: 120 },
        { title: "Cited for", dataIndex: "supporting", width: 170, render: (s) => s.join(", ") || "none" },
        { title: "Cited against", dataIndex: "contradicting", width: 120, render: (s) => s.join(", ") || "none" },
      ]}
    />
  );
}

function VerifyPanel({ phase }) {
  const verdicts = phase.update.verification?.verdicts ?? [];
  if (!verdicts.length) return <Empty description="No labels: the Verifier failed or there was nothing to check" />;
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Text type="secondary">The Verifier never sees what was cited. Code then computes confidence from these labels.</Text>
      <Row gutter={[12, 12]}>
        {verdicts.map((v) => (
          <Col key={v.hypothesis_id} xs={24} lg={12} xxl={8}>
            <Card size="small" title={v.hypothesis_id}>
              {v.rows.length === 0 && <Text type="secondary">No row matters for this cause</Text>}
              {v.rows.map((r) => (
                <div key={r.evidence_id} className="claim">
                  <Tag color={LABEL_COLOR[r.label]}>{r.label}</Tag>
                  <Text code>{r.evidence_id}</Text> <Text type="secondary">{r.reason}</Text>
                </div>
              ))}
            </Card>
          </Col>
        ))}
      </Row>
    </Space>
  );
}

function CritiquePanel({ phase }) {
  const c = phase.update.critique;
  if (!c) return <Alert type="error" showIcon message="The Critic failed, so the run ends inconclusive" />;
  const color = c.verdict === "accept" ? "green" : c.verdict === "investigate" ? "gold" : "red";
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Space>
        <Tag color={color} style={{ fontSize: 14, padding: "2px 10px" }}>{c.verdict}</Tag>
        {phase.update.stop_reason ? <Text type="secondary">Stops here: {phase.update.stop_reason}</Text> : <Text type="secondary">Requests another round</Text>}
      </Space>
      {c.objections.map((o, i) => (
        <Alert key={i} type="info" message={o} />
      ))}
      {c.requests.length > 0 && (
        <Table
          size="small"
          pagination={false}
          rowKey={(r, i) => i}
          dataSource={c.requests}
          columns={[
            { title: "Asks", dataIndex: "agent", width: 130, render: (a) => <Tag color={a === "observability" ? "blue" : "purple"}>{a}</Tag> },
            { title: "For", dataIndex: "question" },
          ]}
        />
      )}
    </Space>
  );
}

function ReportPanel({ phase, trace, reveal }) {
  const r = phase.update.report ?? trace.report;
  const truth = trace.truth;
  const top = r.hypotheses[0];
  const right =
    truth.outcome === "inconclusive" ? r.outcome === "inconclusive" : r.outcome === "conclusive" && top && top.category === truth.category && top.component === truth.component;
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Space wrap>
        <Tag color={r.outcome === "conclusive" ? "green" : "gold"} style={{ fontSize: 15, padding: "3px 12px" }}>
          {r.outcome.toUpperCase()}
        </Tag>
        {r.outcome_reasons.map((x) => (
          <Tag key={x}>{x}</Tag>
        ))}
        {reveal && <Tag color={right ? "green" : "red"}>{right ? "✓ matches the correct answer" : "✕ does not match the correct answer"}</Tag>}
      </Space>
      <Paragraph style={{ marginBottom: 0 }}>{r.summary}</Paragraph>
      <Table
        size="small"
        pagination={false}
        rowKey="id"
        dataSource={r.hypotheses}
        columns={[
          { title: "Rank", render: (_, __, i) => i + 1, width: 60 },
          { title: "Cause", dataIndex: "statement" },
          { title: "Confidence", dataIndex: "confidence", width: 110, render: (c) => <Tag color={c === "high" ? "green" : c === "medium" ? "blue" : "default"}>{c}</Tag> },
          { title: "Verified for", dataIndex: "verified_supporting", width: 170, render: (s) => s.join(", ") || "none" },
          { title: "Against", dataIndex: "verified_contradicting", width: 110, render: (s) => s.join(", ") || "none" },
          { title: "Net support", dataIndex: "net_support", width: 100 },
        ]}
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card size="small" title={`Facts ${r.facts.length} · Inferences ${r.inferences.length} · Assumptions ${r.assumptions.length}`}>
            {[...r.facts.slice(0, 3), ...r.assumptions.slice(0, 2)].map((c, i) => (
              <div key={i} className="claim">
                <Tag color={c.kind === "fact" ? "green" : "default"}>{c.kind}</Tag>
                {c.text}
              </div>
            ))}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card size="small" title="Recommended actions">
            {r.actions.map((a, i) => (
              <div key={i} className="claim">
                {a.requires_human_approval ? <Tag color="orange">needs approval</Tag> : <Tag>low risk</Tag>}
                {a.description}
              </div>
            ))}
            {r.gaps.length > 0 && <Alert type="warning" style={{ marginTop: 8 }} message={`Evidence gaps: ${r.gaps.slice(0, 4).join("; ")}`} />}
          </Card>
        </Col>
      </Row>
    </Space>
  );
}

export default function Flow({ traces }) {
  // Assignment incidents first, then blind ones, so the clean case opens first.
  const ids = Object.keys(traces).sort((a, b) => (a.startsWith("INC") === b.startsWith("INC") ? a.localeCompare(b) : a.startsWith("INC") ? -1 : 1));
  const [id, setId] = useState(ids[0]);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [reveal, setReveal] = useState(false);
  const trace = traces[id];
  const phases = useMemo(() => buildPhases(trace), [trace]);
  const phase = phases[index];
  const last = phases.length - 1;

  useEffect(() => {
    if (!playing) return undefined;
    if (index >= last) {
      setPlaying(false);
      return undefined;
    }
    const timer = setTimeout(() => setIndex(index + 1), 1600);
    return () => clearTimeout(timer);
  }, [playing, index, last]);

  const choose = (next) => {
    setId(next);
    setIndex(0);
    setPlaying(false);
    setReveal(false);
  };
  const usage = trace.report.usage;
  const panels = {
    incident: <IncidentPanel trace={trace} />,
    orchestrate: <PlanPanel phase={phase} />,
    run_task: <EvidencePanel phase={phase} />,
    reason: <HypothesesPanel phase={phase} />,
    verify: <VerifyPanel phase={phase} />,
    criticize: <CritiquePanel phase={phase} />,
    finalize: <ReportPanel phase={phase} trace={trace} reveal={reveal} />,
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card size="small">
        <Row gutter={[16, 12]} align="middle">
          <Col flex="auto">
            <Segmented
              value={id}
              onChange={choose}
              options={ids.map((key) => ({ value: key, label: `${key}: ${traces[key].incident.title}` }))}
            />
          </Col>
          <Col>
            <Space>
              <Text type="secondary">
                Recorded run: {trace.seconds}s, {usage.input_tokens + usage.output_tokens} tokens, {usage.tool_calls} tool calls
              </Text>
              <Button size="small" onClick={() => setReveal(!reveal)}>
                {reveal ? "Hide answer" : "Reveal answer"}
              </Button>
            </Space>
          </Col>
        </Row>
        {reveal && (
          <Alert
            style={{ marginTop: 12 }}
            type="info"
            message={
              trace.truth.outcome === "conclusive"
                ? `Correct answer: ${trace.truth.category} in ${trace.truth.component}. Key evidence: ${trace.truth.key_evidence.join(", ")}.`
                : "Correct answer: inconclusive. The evidence cannot establish a cause."
            }
            description={trace.scenario}
          />
        )}
      </Card>

      <Card size="small">
        <Steps size="small" current={phase.stage} items={STAGES} onChange={(s) => setIndex(Math.max(0, phases.findIndex((p) => p.stage === s)))} />
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={17}>
          <Card
            title={
              <Space>
                <span>{phaseTitle(phase)}</span>
                {phase.round > 0 && <Tag>round {phase.round}</Tag>}
                {phase.node !== "incident" && <Text type="secondary">{AGENT_LABEL[phase.node] ?? ""} · at {phase.t}s</Text>}
              </Space>
            }
            extra={
              <Space>
                <Button icon={<UndoOutlined />} onClick={() => { setIndex(0); setPlaying(false); }} aria-label="Restart" />
                <Button icon={<StepBackwardOutlined />} disabled={index === 0} onClick={() => setIndex(index - 1)} aria-label="Previous step" />
                <Button type="primary" icon={playing ? <PauseOutlined /> : <CaretRightOutlined />} onClick={() => setPlaying(!playing)}>
                  {playing ? "Pause" : "Play"}
                </Button>
                <Button icon={<StepForwardOutlined />} disabled={index === last} onClick={() => setIndex(index + 1)} aria-label="Next step" />
              </Space>
            }
          >
            <Slider min={0} max={last} value={index} onChange={setIndex} tooltip={{ formatter: (v) => phaseTitle(phases[v]) }} />
            {panels[phase.node]}
          </Card>
        </Col>
        <Col xs={24} xl={7}>
          <Card title="Timeline" size="small" className="timeline-card">
            <Timeline
              items={phases.map((p, i) => ({
                color: i === index ? "blue" : i < index ? "green" : "gray",
                children: (
                  <a onClick={() => setIndex(i)} className={i === index ? "timeline-active" : ""}>
                    <Text type={i > index ? "secondary" : undefined} strong={i === index}>
                      {phaseTitle(p)}
                    </Text>
                    <br />
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {p.node === "incident" ? "start" : `${p.t}s`}
                      {p.round > 0 ? ` · round ${p.round}` : ""}
                    </Text>
                  </a>
                ),
              }))}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
