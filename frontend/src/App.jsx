import { Alert, ConfigProvider, Layout, Spin, Tabs, theme, Typography } from "antd";
import { useEffect, useState } from "react";
import Flow from "./Flow.jsx";
import { Checks, Data, Requirements, Results } from "./pages.jsx";

const { Header, Content } = Layout;

function useDarkMode() {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState(query.matches);
  useEffect(() => {
    const listen = (e) => setDark(e.matches);
    query.addEventListener("change", listen);
    return () => query.removeEventListener("change", listen);
  }, [query]);
  return dark;
}

async function loadAll() {
  const json = (path) => fetch(path).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${path}: HTTP ${r.status}`))));
  const [data, ids] = await Promise.all([json("data.json"), json("traces/index.json")]);
  const traces = Object.fromEntries(await Promise.all(ids.map(async (id) => [id, await json(`traces/${id}.json`)])));
  return { data, traces };
}

export default function App() {
  const dark = useDarkMode();
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState(() => location.hash.slice(1) || "flow");
  useEffect(() => {
    loadAll().then(setState).catch((e) => setError(String(e)));
  }, []);
  useEffect(() => {
    location.hash = tab;
  }, [tab]);

  const pages = state && [
    { key: "flow", label: "Investigation replay", children: <Flow traces={state.traces} /> },
    { key: "results", label: "Results", children: <Results data={state.data} /> },
    { key: "data", label: "Test incidents", children: <Data data={state.data} /> },
    { key: "checks", label: "Offline checks", children: <Checks data={state.data} /> },
    { key: "requirements", label: "Requirements", children: <Requirements /> },
  ];

  return (
    <ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm, token: { colorPrimary: dark ? "#3987e5" : "#2a78d6", borderRadius: 8 } }}>
      <Layout className={dark ? "app dark" : "app"}>
        <Header className="app-header">
          <Typography.Title level={4} style={{ margin: 0 }}>
            Multi-Agent Incident Investigator
          </Typography.Title>
          <Typography.Text type="secondary">Investigates simulated production incidents, from report to evidence-backed root cause</Typography.Text>
        </Header>
        <Content className="app-content">
          {error && <Alert type="error" showIcon message={`Could not load data: ${error}`} />}
          {!state && !error && <Spin size="large" />}
          {pages && <Tabs activeKey={tab} onChange={setTab} items={pages} size="large" />}
        </Content>
      </Layout>
    </ConfigProvider>
  );
}
