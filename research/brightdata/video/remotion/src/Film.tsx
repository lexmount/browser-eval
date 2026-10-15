import React, { createContext, useContext } from "react";
import {
  AbsoluteFill,
  Img,
  Html5Audio,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import data from "./timeline.json";
const C = {
  ink: "#123a46",
  navy: "#08376f",
  blue: "#087fb4",
  teal: "#008e87",
  amber: "#cf7900",
  paper: "#faf8f2",
  muted: "#607c86",
  line: "#d4e1e3",
};
const MotionContext = createContext(1);
const Font = '"PingFang SC", "Microsoft YaHei", sans-serif';
type S = (typeof data.scenes)[number];
const Label = ({
  x,
  y,
  w = 1500,
  text,
  size = 34,
  color = C.ink,
  bold = false,
  align = "left",
}: {
  x: number;
  y: number;
  w?: number;
  text: string;
  size?: number;
  color?: string;
  bold?: boolean;
  align?: "left" | "center";
}) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width: w,
      fontSize: size,
      fontWeight: bold ? 650 : 400,
      lineHeight: 1.45,
      color,
      textAlign: align,
      whiteSpace: "pre-line",
      boxSizing: "border-box",
    }}
  >
    {text}
  </div>
);
function Cell({
  id,
  x,
  y,
  w,
  h = 110,
  title,
  sub = "",
  active = false,
  color = C.teal,
  small = false,
}: {
  id: string;
  x: number;
  y: number;
  w: number;
  h?: number;
  title: string;
  sub?: string;
  active?: boolean;
  color?: string;
  small?: boolean;
}) {
  const progress = useContext(MotionContext);
  return (
    <div
      data-target={id}
      data-active={active}
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        borderRadius: 16,
        background: active ? "#e6f3ee" : "#fff",
        border: `${active ? 4 : 2}px solid ${active ? color : C.line}`,
        boxShadow: active ? "0 9px 24px #15343b12" : "none",
        padding: small ? "14px 16px" : "21px 25px",
        color: C.ink,
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      {active && (
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            height: 5,
            width: `${progress * 100}%`,
            background: color,
          }}
        />
      )}
      <div
        style={{
          fontSize: small ? 27 : 36,
          fontWeight: 650,
          color: active ? color : C.ink,
          lineHeight: 1.25,
        }}
      >
        {title}
      </div>
      {sub && (
        <div
          style={{
            fontSize: small ? 22 : 29,
            lineHeight: 1.35,
            marginTop: 10,
            whiteSpace: "pre-line",
            boxSizing: "border-box",
          }}
        >
          {sub.replace(/\\n/g, "\n")}
        </div>
      )}
    </div>
  );
}
function Arrow({
  x1,
  y1,
  x2,
  y2,
  active = false,
  routeX,
}: {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  active?: boolean;
  routeX?: number;
}) {
  const progress = useContext(MotionContext);
  return (
    <svg
      style={{
        position: "absolute",
        inset: 0,
        width: 1920,
        height: 1080,
        pointerEvents: "none",
      }}
    >
      <defs>
        <marker
          id={active ? "a1" : "a0"}
          markerWidth="5"
          markerHeight="5"
          refX="4.5"
          refY="2.5"
          orient="auto"
        >
          <path d="M0,0 L5,2.5 L0,5" fill={active ? C.teal : "#b5cacc"} />
        </marker>
      </defs>
      <path
        d={
          routeX
            ? `M${x1},${y1} H${routeX} V${y2} H${x2}`
            : `M${x1},${y1} L${x2},${y2}`
        }
        fill="none"
        stroke={active ? C.teal : "#b5cacc"}
        strokeWidth={active ? 4 : 2}
        pathLength={1}
        strokeDasharray={active ? "1" : undefined}
        strokeDashoffset={active ? 1 - progress : 0}
        markerEnd={`url(#${active ? "a1" : "a0"})`}
      />
    </svg>
  );
}
function SourceMap({
  focus,
  recap = false,
}: {
  focus: string;
  recap?: boolean;
}) {
  const x = recap ? 65 : 280,
    y = 170,
    w = recap ? 1210 : 1360,
    h = (w * 941) / 1672;
  const regions: Record<string, number[]> = {
    access: [0.11, 0.67, 0.53, 0.11],
    extraction: [0.11, 0.54, 0.78, 0.105],
    data: [0.11, 0.415, 0.78, 0.1],
    agent: [0.71, 0.66, 0.18, 0.13],
    intelligence: [0.11, 0.3, 0.78, 0.1],
  };
  const region = regions[focus];
  const progress = useContext(MotionContext);
  return (
    <>
      <Img
        src={staticFile("architecture.png")}
        style={{ position: "absolute", left: x, top: y, width: w, height: h }}
      />
      {region && (
        <div
          data-target={focus}
          data-active="true"
          style={{
            position: "absolute",
            left: x + region[0] * w,
            top: y + region[1] * h,
            width: region[2] * w,
            height: region[3] * h,
            border: "4px solid #d58000",
            borderRadius: 9,
            boxSizing: "border-box",
            boxShadow: "0 0 0 3px #ffffffbb",
            opacity: progress,
          }}
        />
      )}
      {recap && (
        <>
          <Label
            x={1320}
            y={215}
            w={520}
            text="同一套基础，不同层次的产品"
            size={36}
            bold
          />
          {[
            ["access", "访问网站"],
            ["extraction", "整理成字段"],
            ["data", "保存并持续刷新"],
            ["agent", "支持业务与 Agent"],
          ].map(([id, t], i) => (
            <Cell
              key={id}
              id={id}
              x={1320}
              y={300 + i * 124}
              w={500}
              h={105}
              title={t}
              active={
                focus === id || (id === "agent" && focus === "intelligence")
              }
              small
            />
          ))}
        </>
      )}
    </>
  );
}
const ladder = [
  ["access", "Access", "网站可获得"],
  ["extraction", "Extraction", "字段可使用"],
  ["data", "Data Product", "数据可持续"],
  ["intelligence", "Intelligence", "支持业务判断"],
];
function Route({ focus }: { focus: string }) {
  return (
    <>
      {ladder.map(([id, en, zh], i) => (
        <React.Fragment key={id}>
          {i > 0 && (
            <Arrow
              x1={120 + i * 440 - 55}
              y1={410}
              x2={120 + i * 440 - 12}
              y2={410}
              active={focus === id}
            />
          )}
          <Cell
            id={id}
            x={110 + i * 440}
            y={300}
            w={380}
            h={220}
            title={en}
            sub={zh}
            color={[C.navy, C.blue, C.teal, C.amber][i]}
            active={focus === id || focus === "whole"}
          />
        </React.Fragment>
      ))}
      <Arrow x1={300} y1={530} x2={300} y2={640} active={focus === "agent"} />
      <Cell
        id="agent"
        x={110}
        y={660}
        w={550}
        h={150}
        title="Agent Execution"
        sub="保留网页交互与多步操作"
        active={focus === "agent"}
      />
      <div
        style={{
          position: "absolute",
          left: 800,
          top: 650,
          display: "flex",
          gap: 30,
        }}
      >
        {["共用能力", "产品与客户", "Lexmount 启发"].map((t, i) => (
          <div
            key={t}
            style={{
              padding: "25px 32px",
              fontSize: 29,
              borderTop: `4px solid ${C.teal}`,
              color: C.ink,
              background: "#eff4f1",
            }}
          >
            {i + 1}　{t}
          </div>
        ))}
      </div>
    </>
  );
}
function History({ focus }: { focus: string }) {
  const items = [
    ["network", "2014", "住宅代理起家"],
    ["expansion", "2019", "网站解锁"],
    ["rename", "2021", "Luminati 更名"],
    ["expansion2", "2022–23", "数据集与浏览器"],
    ["revenue", "2025", "年化收入 > $3 亿"],
  ];
  return (
    <>
      <Label
        x={90}
        y={200}
        text="Bright Data  /  原名 Luminati"
        size={48}
        bold
      />
      <Label
        x={90}
        y={275}
        text="把访问网站、采集数据和持续维护做成服务"
        size={34}
        color={C.muted}
      />
      <Arrow x1={160} y1={500} x2={1750} y2={500} active />
      {items.map(([id, year, t], i) => (
        <React.Fragment key={id}>
          <div
            data-target={id}
            data-active={focus === id}
            style={{
              position: "absolute",
              left: 130 + i * 355,
              top: 480,
              width: 40,
              height: 40,
              borderRadius: 30,
              background: focus === id ? C.amber : C.teal,
            }}
          />
          <Label
            x={90 + i * 355}
            y={395}
            w={400}
            text={year}
            size={40}
            bold
            color={focus === id ? C.amber : C.navy}
          />
          <Label x={90 + i * 355} y={560} w={340} text={t} size={31} bold />
        </React.Fragment>
      ))}
      <Label
        x={100}
        y={755}
        w={1740}
        text="从网络供给，逐步扩展到数据与 AI 所需的基础设施"
        size={39}
        bold
        color={C.navy}
      />
      <Label
        x={100}
        y={830}
        text="收入采用公司披露的年化运行率；不等同于经审计的 SaaS ARR。"
        size={25}
        color={C.muted}
      />
    </>
  );
}
const stackRows = [
  ["intelligence", "业务使用", "Retail Intelligence · Managed Data"],
  ["data", "数据产品", "Datasets · Web Archive · Firehose"],
  ["extraction", "提取与适配", "Web Scraper API · Scraper Studio"],
  ["access", "访问与反爬", "Web Unlocker · SERP · Browser API"],
  ["network", "网络供给", "住宅 / ISP / 数据中心 · 地区与运营商"],
];
const detail: Record<string, [string, string, string]> = {
  network: ["真实网络出口", "IP 类型与地区定位", "解决“从哪里访问”"],
  access: ["取得目标页面", "封锁、验证码、指纹、重试", "解决“能否持续访问”"],
  extraction: ["页面变成字段", "站点适配与字段定义", "解决“数据能否直接使用”"],
  data: ["记录形成数据资产", "保存历史、校验与持续刷新", "解决“能否长期复用”"],
  intelligence: ["数据进入业务", "零售分析与托管数据", "支持判断与决策"],
  agent: ["浏览器执行分支", "点击、输入、翻页、表单", "与字段提取并行"],
};
function Stack({ focus }: { focus: string }) {
  let d = detail[focus] || detail.access;
  const selected = stackRows.find(([id]) => id === focus);
  return (
    <>
      {stackRows.map(([id, title, sub], i) => (
        <React.Fragment key={id}>
          <Cell
            id={id}
            x={85}
            y={180 + i * 120}
            w={1090}
            h={104}
            title={title}
            sub={sub}
            active={focus === id}
            small
            color={id === "intelligence" ? C.amber : C.teal}
          />
          {i < 4 && (
            <Arrow
              x1={1220}
              y1={296 + i * 120}
              x2={1220}
              y2={198 + i * 120}
              active={focus === id}
            />
          )}
        </React.Fragment>
      ))}
      <Cell
        id="agent"
        x={1320}
        y={645}
        w={510}
        h={165}
        title="Agent Execution"
        sub="远程浏览器 · 多步操作"
        active={focus === "agent"}
      />
      <Arrow x1={1185} y1={590} x2={1370} y2={640} active={focus === "agent"} />
      <Label x={1320} y={180} w={500} text={selected ? `${selected[1]} · 作用` : "浏览器执行 · 作用"} size={26} color={C.muted} />
      <Label
        x={1320}
        y={235}
        w={500}
        text={d[0]}
        size={42}
        bold
        color={C.teal}
      />
      <Label x={1320} y={330} w={500} text={d[1]} size={33} />
      <Label x={1320} y={435} w={500} text={d[2]} size={30} color={C.muted} />
      <Label x={1320} y={560} w={510} text="共用访问能力，直接操作网页" size={27} color={C.teal} />
      <Label
        x={90}
        y={830}
        text="贯穿各层：数据质量与新鲜度　／　合规、权限与企业运维"
        size={30}
        color={C.navy}
      />
    </>
  );
}
const nodes: Record<string, [number, number, string, string]> = {
  network: [65, 540, "IP / 网络供给", "住宅 · ISP · 数据中心"],
  geo: [65, 240, "地区与网络定位", "Geo · ASN · Carrier"],
  access: [405, 540, "访问与反爬", "Unlocker · SERP\nBrowser API"],
  extract: [745, 390, "提取与适配", "Web Scraper · Studio"],
  browser: [745, 690, "浏览器执行", "Browser / Agent Browser"],
  datasets: [1085, 210, "数据集与快照", "Datasets · Snapshots"],
  firehose: [1085, 365, "持续采集", "Data Firehose"],
  archive: [1085, 520, "历史网页", "Web Archive"],
  entries: [1085, 735, "AI 调用入口", "MCP · CLI\nSearch & Extract"],
  intelligence: [1510, 160, "行业分析", "Retail Intelligence"],
  managed: [1510, 315, "托管数据", "Managed Data"],
  search: [1510, 470, "专门搜索", "Business / Video Search"],
  business: [1510, 625, "业务决策", "分析与研究"],
  agents: [1510, 780, "客户 Agent / RAG", "网页行动与实时信息"],
};
const edges = [
  ["network", "geo"],
  ["network", "access"],
  ["access", "extract"],
  ["access", "browser"],
  ["extract", "datasets"],
  ["extract", "firehose"],
  ["extract", "archive"],
  ["datasets", "intelligence"],
  ["datasets", "managed"],
  ["firehose", "managed"],
  ["archive", "managed"],
  ["datasets", "search"],
  ["archive", "search"],
  ["access", "entries"],
  ["extract", "entries"],
  ["browser", "entries"],
  ["entries", "agents"],
  ["search", "agents"],
  ["intelligence", "business"],
  ["managed", "business"],
];
function Dependency({ focus }: { focus: string }) {
  let group =
    focus === "agent-path"
      ? ["network", "access", "browser", "entries", "agents"]
      : focus === "entry-path"
        ? ["access", "extract", "browser", "entries", "agents"]
        : focus === "data-path"
          ? [
              "network",
              "access",
              "extract",
              "datasets",
              "firehose",
              "archive",
              "intelligence",
              "managed",
              "business",
            ]
          : focus === "access"
            ? ["network", "access", "extract", "browser"]
            : focus === "extraction"
              ? ["extract", "datasets", "firehose", "archive"]
              : focus === "data"
                ? ["datasets", "archive", "intelligence", "search"]
                : Object.keys(nodes);
  return (
    <>
      {edges.map(([a, b], i) => {
        const [x, y] = nodes[a],
          [xx, yy] = nodes[b];
        const aw = a === "entries" ? 360 : 300;
        const on = group.includes(a) && group.includes(b);
        if (!on) return null;
        const same = x === xx;
        return (
          <Arrow
            key={i}
            x1={same && a === "network" ? x + 150 : x + aw + 3}
            y1={same && a === "network" ? y - 3 : y + 66}
            x2={same && a === "network" ? xx + 150 : same ? xx + 310 : xx - 8}
            y2={same && a === "network" ? yy + 140 : yy + 66}
            routeX={same && a !== "network" ? 1870 : undefined}
            active={on}
          />
        );
      })}
      {Object.entries(nodes).map(([id, [x, y, t, sub]]) => (
        <Cell
          key={id}
          id={id}
          x={x}
          y={y}
          w={id === "entries" ? 360 : 300}
          h={140}
          title={t}
          sub={sub}
          small
          active={group.includes(id)}
        />
      ))}
      <Label
        x={60}
        y={890}
        text="按原研究关系图重绘：表示能力复用，并非 Bright Data 内部系统拓扑。"
        size={24}
        color={C.muted}
      />
    </>
  );
}
const products = [
  ["unlocker", "Web Unlocker", "页面响应", "目标 URL → HTML"],
  ["browser", "Browser API", "浏览器会话", "操作代码 → 页面状态 / 截图"],
  ["crawl", "Crawl API", "网页文档", "页面入口 → HTML / Markdown / 文本"],
  ["scraper", "Web Scraper API", "业务记录", "商品 / 公司 → JSON / CSV 等"],
  ["serp", "SERP API", "搜索结果", "查询条件 → 搜索页面 / 解析 JSON"],
];
function Matrix({ focus, recap = false }: { focus: string; recap?: boolean }) {
  return (
    <>
      <Label x={90} y={185} w={470} text="产品" size={26} color={C.muted} />
      <Label
        x={600}
        y={185}
        w={400}
        text="拿到什么"
        size={26}
        color={C.muted}
      />
      <Label
        x={1070}
        y={185}
        w={740}
        text="典型输入与输出"
        size={26}
        color={C.muted}
      />
      {products.map(([id, name, out, flow], i) => (
        <div
          key={id}
          data-target={id}
          data-active={focus === id}
          style={{
            position: "absolute",
            left: 80,
            top: 240 + i * 118,
            width: 1760,
            height: 100,
            background: i % 2 === 0 ? "#edf4f3" : "#fff",
            borderLeft: `6px solid ${focus === id ? C.amber : C.teal}`,
            borderRadius: 10,
            display: "grid",
            gridTemplateColumns: "500px 460px 1fr",
            alignItems: "center",
            padding: "0 28px",
            fontSize: 32,
          }}
        >
          <strong>{name}</strong>
          <span style={{ color: C.teal, fontWeight: 600 }}>{out}</span>
          <span style={{ fontSize: 29 }}>{flow}</span>
        </div>
      ))}
      <Label
        x={95}
        y={860}
        w={1700}
        text={
          recap
            ? "它们分担不同工作，不是必须依次购买的一条流水线。"
            : "这些名字对应不同交付物；下面依次看它们具体做什么。"
        }
        size={30}
        bold
        color={C.navy}
      />
    </>
  );
}
const pro: Record<
  string,
  {
    input: string;
    service: string;
    output: string;
    boundary: string;
    sample: string;
  }
> = {
  unlocker: {
    input: "目标网址 + 地区等访问条件",
    service: "处理访问拦截",
    output: "目标响应内容 · 通常为 HTML",
    boundary: "价格、库存等字段仍需客户解析",
    sample:
      '<html>\n  <h1>商品名称</h1>\n  <span class="price">…</span>\n</html>',
  },
  browser: {
    input: "连接远程 Chrome 会话",
    service: "客户代码控制网页操作",
    output: "页面状态 / DOM / 网络响应 / 截图",
    boundary: "业务结果由客户脚本定义",
    sample:
      "连接 → 打开网页 → 点击 → 读取\n\nPlaywright · Puppeteer · Selenium",
  },
  crawl: {
    input: "页面或站点入口",
    service: "收集网页内容",
    output: "网页文档 · HTML / Markdown / 文本",
    boundary: "本轮未验证通过完整站点递归",
    sample: "原始 URL  /  最终 URL\n标题\nMarkdown 正文\n纯文本  /  页面 HTML",
  },
  scraper: {
    input: "商品、公司或列表等输入",
    service: "Bright Data 维护站点适配与提取",
    output: "结构化业务记录",
    boundary: "客户仍需检查对象、字段和缺值",
    sample:
      "商品名称     价格     币种     库存\n示意商品     129      CNY      有货\n\nJSON · NDJSON · CSV · XLSX · Parquet",
  },
  serp: {
    input: "关键词、国家、语言、搜索类型",
    service: "获取搜索引擎结果",
    output: "原始搜索页面 / 支持类型的 JSON",
    boundary: "搜索结果不等于目标网页全文",
    sample:
      "排名    标题       链接        摘要\n 1      结果 A    example…    …\n 2      结果 B    example…    …",
  },
};
function Product({ id, focus }: { id: string; focus: string }) {
  const p = pro[id];
  const selected = products.find((x) => x[0] === id)!;
  const active =
    focus === "session"
      ? "input"
      : focus === "actions"
        ? "service"
        : focus === "limit"
          ? "boundary"
          : focus;
  return (
    <>
      {products.map(([k, n], i) => (
        <div
          key={k}
          style={{
            position: "absolute",
            left: 55,
            top: 205 + i * 116,
            width: 340,
            height: 92,
            padding: "24px 18px",
            borderRadius: 12,
            fontSize: 28,
            fontWeight: k === id ? 650 : 400,
            color: k === id ? "white" : C.muted,
            background: k === id ? C.teal : "#eaf0ef",
          }}
        >
          {n}
        </div>
      ))}
      <Label
        x={455}
        y={195}
        text={selected[2]}
        w={1330}
        size={40}
        bold
        color={C.navy}
      />
      <Cell
        id="input"
        x={455}
        y={280}
        w={610}
        h={135}
        title="客户提供"
        sub={p.input}
        active={active === "input"}
        small
      />
      <Arrow
        x1={1085}
        y1={345}
        x2={1160}
        y2={345}
        active={active === "service"}
      />
      <Cell
        id="service"
        x={1185}
        y={280}
        w={650}
        h={135}
        title="服务完成"
        sub={p.service}
        active={active === "service"}
        small
      />
      <div
        data-target="output"
        data-active={active === "output"}
        style={{
          position: "absolute",
          left: 455,
          top: 470,
          width: 1380,
          height: 305,
          boxSizing: "border-box",
          padding: "25px 30px",
          borderRadius: 16,
          border: `${active === "output" ? 4 : 2}px solid ${active === "output" ? C.teal : C.line}`,
          background: "#fff",
        }}
      >
        <div style={{ fontSize: 33, fontWeight: 650, color: C.teal }}>
          {p.output}
        </div>
        <pre
          style={{
            fontSize: id === "scraper" ? 29 : 31,
            lineHeight: 1.7,
            color: C.ink,
            fontFamily: id === "unlocker" ? "monospace" : Font,
            marginTop: 15,
            whiteSpace: "pre-wrap",
            marginBottom: 0,
          }}
        >
          {p.sample}
        </pre>
      </div>
      <div
        data-target="boundary"
        data-active={active === "boundary"}
        style={{
          position: "absolute",
          left: 455,
          top: 810,
          width: 1380,
          padding: "16px 22px",
          background: active === "boundary" ? "#fff0d8" : "#f0f0eb",
          borderLeft: `6px solid ${C.amber}`,
          fontSize: 29,
        }}
      >
        {p.boundary}
      </div>
      <Label
        x={58}
        y={850}
        w={330}
        text="示意交付形式"
        size={24}
        color={C.muted}
      />
    </>
  );
}
function Customers({ focus }: { focus: string }) {
  const rows = [
    ["shopgrok", "ShopGrok", "代理 → 增加 Web Unlocker", "按网站难度选产品\n自己保留商品匹配与定价分析"],
    ["yutori", "Yutori", "Browser API", "多个 Agent 并行操作云端浏览器\n自己负责模型和任务决策"],
    ["manatal", "Manatal", "网站采集器", "补全 LinkedIn / GitHub 公开资料\n替换原来的多个采集工具"],
    ["kingston", "Kingston Brass", "Bright Insights", "看板 + 文件导出\n营销、产品、客户管理团队共用"],
  ];
  return (
    <>
      <Label x={80} y={195} text="客户" size={26} color={C.muted} />
      <Label x={470} y={195} text="使用的服务" size={26} color={C.muted} />
      <Label x={1000} y={195} text="怎样用于日常业务" size={26} color={C.muted} />
      {rows.map(([id, n, t, p], i) => (
        <div
          key={id}
          data-target={id}
          data-active={id === focus}
          style={{
            position: "absolute",
            left: 75,
            top: 265 + i * 135,
            width: 1770,
            height: 110,
            display: "grid",
            gridTemplateColumns: "390px 530px 1fr",
            alignItems: "center",
            padding: "0 28px",
            fontSize: 30,
            borderRadius: 12,
            border: `${id === focus ? 4 : 2}px solid ${id === focus ? C.teal : C.line}`,
            background: id === focus ? "#e4f3ed" : "#fff",
          }}
        >
          <strong>{n}</strong>
          <span>{t}</span>
          <span style={{whiteSpace: "pre-line", lineHeight: 1.4}}>{p}</span>
        </div>
      ))}
      <Label
        x={90}
        y={850}
        text="来源：Bright Data 具名客户案例；这些客户的账单与合同金额未披露。"
        size={28}
        color={C.muted}
      />
    </>
  );
}
function Fornova({ focus }: { focus: string }) {
  return (
    <>
      {[
        ["72", "个国家", "不同地区的报价"],
        ["365", "天", "未来入住日期"],
        ["3", "次 / 天", "通常的扫描频率"],
      ].map(([n, u, t], i) => (
        <React.Fragment key={n}>
          <div
            data-target={i === 2 ? "refresh" : "scope"}
            data-active={focus === "scope" || (focus === "refresh" && i === 2)}
            style={{
              position: "absolute",
              left: 100 + i * 610,
              top: 250,
              width: 500,
              height: 290,
              borderTop: `6px solid ${focus === "scope" || (focus === "refresh" && i === 2) ? C.amber : C.teal}`,
              background: "#fff",
              padding: 28,
            }}
          >
            <div style={{ fontSize: 86, fontWeight: 650, color: C.navy }}>
              {n} <span style={{ fontSize: 31 }}>{u}</span>
            </div>
            <div style={{ fontSize: 32, marginTop: 30 }}>{t}</div>
          </div>
          {i < 2 && (
            <Label
              x={630 + i * 610}
              y={350}
              w={60}
              text="×"
              size={55}
              color={C.teal}
            />
          )}
        </React.Fragment>
      ))}
      <Cell
        id="maintenance"
        x={120}
        y={640}
        w={1680}
        h={170}
        title="规模与变化叠加，采集就成了长期维护"
        sub="网站封锁　／　页面变化　／　字段错误　／　故障恢复"
        active={focus === "maintenance"}
        color={C.amber}
      />
      <Label
        x={120}
        y={850}
        text="Fornova 公开案例；三个维度用于解释工作量，不计算独立请求总数。"
        size={25}
        color={C.muted}
      />
    </>
  );
}
function Quality({ focus }: { focus: string }) {
  const qs = [
    ["success", "成功率", "是否取得目标结果"],
    ["coverage", "来源覆盖", "关键网站是否覆盖"],
    ["freshness", "新鲜度", "是否按需更新"],
    ["correctness", "字段正确", "对象、单位、含义"],
    ["maintenance", "维护响应", "站点变化后能否恢复"],
    ["responsibility", "权限与责任", "权限、支持、故障归属"],
  ];
  return (
    <>
      {qs.map(([id, t, sub], i) => (
        <Cell
          key={id}
          id={id}
          x={95 + (i % 3) * 605}
          y={230 + Math.floor(i / 3) * 225}
          w={540}
          h={175}
          title={t}
          sub={sub}
          active={focus === id || focus === "quality"}
        />
      ))}
      <Cell
        id="result"
        x={110}
        y={735}
        w={1700}
        h={120}
        title="客户付费，是为了持续获得合格结果，并有人负责维护"
        active={focus === "result"}
        color={C.navy}
      />
    </>
  );
}
function AI({ focus }: { focus: string }) {
  return (
    <>
      {[
        ["labs", "14 / 20", "顶级 LLM 实验室"],
        ["companies", "7 / 10", "AI 原生公司"],
        ["agents", "1 亿+ / 天", "AI Agent 交互"],
      ].map(([id, n, t], i) => (
        <Cell
          key={id}
          id={id}
          x={100 + i * 610}
          y={270}
          w={510}
          h={260}
          title={n}
          sub={t}
          active={id === focus}
          color={C.navy}
        />
      ))}
      <Label
        x={115}
        y={600}
        text="Bright Data 公司自报 · 2025 年披露"
        size={29}
        color={C.muted}
      />
      <Cell
        id="direction"
        x={110}
        y={700}
        w={1700}
        h={145}
        title="说明战略方向：数据与网页执行进入 AI 工作流"
        sub="这些数字不等于某个产品的收入占比。"
        active={["direction", "boundary"].includes(focus)}
        small
      />
    </>
  );
}
function Lexmount({ focus }: { focus: string }) {
  const execution = ["execution", "loop", "whole"].includes(focus),
    extraction = ["extraction", "loop", "whole"].includes(focus),
    quality = ["quality", "loop"].includes(focus);
  return (
    <>
      <Label
        x={90}
        y={175}
        w={1700}
        text="已有能力为实线节点；连接表示建议形成的协作关系"
        size={25}
        color={C.muted}
      />
      <Cell
        id="execution"
        x={100}
        y={580}
        w={1120}
        h={160}
        title="Moli · 浏览器运行能力"
        sub="运行网页、维持会话、执行操作"
        active={execution}
        color={C.navy}
      />
      <Cell
        id="session"
        x={100}
        y={330}
        w={510}
        h={170}
        title="Cloud Browser"
        sub="可控制的浏览器会话"
        active={execution}
        color={C.navy}
      />
      <Cell
        id="extraction"
        x={710}
        y={330}
        w={510}
        h={170}
        title="Web Fetch"
        sub="标题、正文、链接、图片"
        active={extraction}
        color={C.blue}
      />
      <Arrow x1={350} y1={570} x2={350} y2={510} active={execution} />
      <Arrow x1={960} y1={570} x2={960} y2={510} active={extraction} />
      <Label
        x={100}
        y={230}
        w={510}
        text="网页操作 / Agent 任务"
        size={32}
        bold
        align="center"
        color={C.navy}
      />
      <Label
        x={710}
        y={230}
        w={510}
        text="网页阅读 / 信息提取"
        size={32}
        bold
        align="center"
        color={C.blue}
      />
      <Cell
        id="quality"
        x={1370}
        y={330}
        w={440}
        h={410}
        title="browser-eval / Lexbench"
        sub="跨来源与浏览器评测\n\n发现失败与质量差异\n\n修复后回归验证"
        active={quality}
        small
        color={C.amber}
      />
      <Arrow x1={1240} y1={410} x2={1355} y2={410} active={quality} />
      <Arrow x1={1355} y1={660} x2={1240} y2={660} active={quality} />
      <div
        data-target="loop"
        data-active={focus === "loop"}
        style={{
          position: "absolute",
          left: 100,
          top: 805,
          width: 1710,
          borderTop: `4px solid ${quality ? C.amber : C.line}`,
          paddingTop: 22,
          fontSize: 34,
          fontWeight: 600,
          color: C.ink,
        }}
      >
        执行提供网页　→　提取产出结果　→　评测反馈问题　→　修复后再次验证
      </div>
    </>
  );
}
function Fields({ focus }: { focus: string }) {
  return (
    <>
      <Cell
        id="tasks"
        x={90}
        y={240}
        w={470}
        h={220}
        title="重复客户任务"
        sub="商品监测、公司资料\n同类来源反复出现"
        active={focus === "tasks"}
      />
      <Arrow
        x1={580}
        y1={350}
        x2={675}
        y2={350}
        active={["fields", "record"].includes(focus)}
      />
      <Cell
        id="fields"
        x={705}
        y={240}
        w={510}
        h={220}
        title="可复用字段服务"
        sub="同一业务对象\n同一字段含义"
        active={["fields", "record"].includes(focus)}
      />
      <Arrow x1={1230} y1={350} x2={1320} y2={350} active={focus === "data"} />
      <Cell
        id="data"
        x={1350}
        y={240}
        w={480}
        h={220}
        title="数据产品"
        sub="持续刷新、历史记录\n跨任务可追溯"
        active={focus === "data"}
      />
      <Cell
        id="record"
        x={100}
        y={580}
        w={1100}
        h={210}
        title="每条记录都要知道它是什么、来自哪里"
        sub="对象与字段　／　来源 URL　／　地区与时间"
        active={focus === "record"}
        small
      />
      <Cell
        id="maintenance"
        x={1270}
        y={580}
        w={550}
        h={210}
        title="持续维护"
        sub="站点变化与缺值\n每条合格记录的成本"
        active={focus === "maintenance"}
        small
      />
      <Label
        x={100}
        y={850}
        text="先证明多个真实任务能复用，再把记录积累成产品。"
        size={33}
        bold
        color={C.navy}
      />
    </>
  );
}
function Reuse({ focus }: { focus: string }) {
  const rows = [
    [
      "current",
      "已有基础",
      "Cloud Browser · Web Fetch",
      "会话与操作 · 网页内容",
      "Moli / 浏览器运行时 + 评测",
    ],
    [
      "near",
      "相邻机会",
      "字段采集服务",
      "商品、公司等结构化记录",
      "复用执行与提取；补站点适配",
    ],
    [
      "middle",
      "需求验证后",
      "持续数据服务",
      "刷新、变化记录、历史查询",
      "复用字段服务；补调度与存储",
    ],
    [
      "upper",
      "候选方向",
      "行业应用 / Agent 工作流",
      "业务判断或多步任务结果",
      "复用数据或执行；补业务能力",
    ],
  ];
  return (
    <>
      <Label x={90} y={183} text="阶段" w={220} size={26} color={C.muted} />
      <Label
        x={360}
        y={183}
        text="产品位置"
        w={560}
        size={26}
        color={C.muted}
      />
      <Label
        x={960}
        y={183}
        text="客户得到什么"
        w={430}
        size={26}
        color={C.muted}
      />
      <Label
        x={1410}
        y={183}
        text="依靠什么继续生长"
        w={420}
        size={26}
        color={C.muted}
      />
      {rows.map(([id, status, name, out, base], i) => (
        <div
          key={id}
          data-target={id}
          data-active={focus === id}
          style={{
            position: "absolute",
            left: 80,
            top: 240 + i * 140,
            width: 1760,
            height: 120,
            boxSizing: "border-box",
            padding: "18px 24px",
            display: "grid",
            gridTemplateColumns: "250px 600px 450px 1fr",
            alignItems: "center",
            borderRadius: 14,
            border: `${focus === id ? 4 : 2}px ${i === 0 ? "solid" : "dashed"} ${focus === id ? C.teal : C.line}`,
            background: focus === id ? "#e5f3ee" : "#fff",
          }}
        >
          <span style={{ fontSize: 27, color: i === 0 ? C.navy : C.muted }}>
            {status}
          </span>
          <strong style={{ fontSize: 31, color: C.ink }}>{name}</strong>
          <span style={{ fontSize: 28 }}>{out}</span>
          <span style={{ fontSize: 25, color: C.muted }}>{base}</span>
        </div>
      ))}
      <div
        data-target="shared"
        data-active={focus === "shared"}
        style={{
          position: "absolute",
          left: 95,
          top: 835,
          width: 1730,
          padding: "15px 20px",
          boxSizing: "border-box",
          background: focus === "shared" ? "#fff0d8" : "#edf2ef",
          borderLeft: `5px solid ${C.amber}`,
          fontSize: 29,
        }}
      >
        贯穿各层：browser-eval / Lexbench 质量验证 · 来源与时间 · 维护成本
      </div>
    </>
  );
}
function Roadmap({ focus }: { focus: string }) {
  return (
    <>
      {[
        ["near", "近期", "执行 + 字段 + 验收", "先连成稳定交付"],
        ["middle", "中期", "持续刷新 + 历史", "只沉淀反复使用的数据"],
        ["upper", "更上层", "行业应用 / Agent", "由明确客户场景决定"],
      ].map(([id, t, s, n], i) => (
        <React.Fragment key={id}>
          <Cell
            id={id}
            x={100 + i * 610}
            y={300}
            w={520}
            h={300}
            title={t}
            sub={s + "\n\n" + n}
            active={focus === id}
            color={[C.navy, C.teal, C.amber][i]}
          />
          {i < 2 && (
            <Arrow
              x1={640 + i * 610}
              y1={445}
              x2={685 + i * 610}
              y2={445}
              active
            />
          )}
        </React.Fragment>
      ))}
      <Cell
        id="validation"
        x={110}
        y={725}
        w={1700}
        h={130}
        title="每向上一层，都先由真实客户任务证明复用价值"
        active={focus === "validation"}
        color={C.navy}
      />
    </>
  );
}
function Scene({ s }: { s: S }) {
  const f = useCurrentFrame();
  const t = f / 30 + s.start;
  const ev = data.events.filter((e) => e.scene === s.id && e.start <= t).at(-1);
  const focus = ev?.target || "whole";
  const relevant = data.events.filter((e) => e.scene === s.id && e.start <= t);
  let change = ev?.start || s.start;
  for (let i = relevant.length - 2; i >= 0; i--) {
    if (relevant[i].target !== focus) break;
    change = relevant[i].start;
  }
  const motion = interpolate(t - change, [0, 0.55], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const fade = interpolate(f, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  let body;
  switch (s.id) {
    case "history":
      body = <History focus={focus} />;
      break;
    case "route":
      body = <Route focus={focus} />;
      break;
    case "source":
      body = <SourceMap focus={focus} />;
      break;
    case "stack":
      body = <Stack focus={focus} />;
      break;
    case "dependency":
      body = <Dependency focus={focus} />;
      break;
    case "matrix":
    case "matrix-recap":
      body = <Matrix focus={focus} recap={s.id === "matrix-recap"} />;
      break;
    case "unlocker":
    case "browser":
    case "crawl":
    case "scraper":
    case "serp":
      body = <Product id={s.id} focus={focus} />;
      break;
    case "customers":
      body = <Customers focus={focus} />;
      break;
    case "fornova":
      body = <Fornova focus={focus} />;
      break;
    case "quality":
      body = <Quality focus={focus} />;
      break;
    case "ai":
      body = <AI focus={focus} />;
      break;
    case "lexmount":
      body = <Lexmount focus={focus} />;
      break;
    case "fields":
      body = <Fields focus={focus} />;
      break;
    case "reuse":
      body = <Reuse focus={focus} />;
      break;
    case "recap":
      body = <SourceMap focus={focus} recap />;
      break;
    case "roadmap":
      body = <Roadmap focus={focus} />;
      break;
  }
  return (
    <AbsoluteFill data-scene={s.id} style={{ opacity: fade }}>
      <Label x={75} y={90} w={1760} text={s.title} size={46} bold />
      <MotionContext.Provider value={motion}>{body}</MotionContext.Provider>
    </AbsoluteFill>
  );
}
export function Film({ withAudio = true }: { withAudio?: boolean }) {
  const f = useCurrentFrame(),
    t = f / 30;
  const scene =
    data.scenes.find((s) => t >= s.start && t < s.end) || data.scenes.at(-1)!;
  const caption = data.captions.find((c) => t >= c.start && t < c.end);
  const reviewing = t >= scene.speechEnd;
  const holdProgress = Math.min(
    1,
    Math.max(0, (t - scene.speechEnd) / (scene.end - scene.speechEnd)),
  );
  return (
    <AbsoluteFill
      style={{ background: C.paper, fontFamily: Font, color: C.ink }}
    >
      {withAudio && <Html5Audio src={staticFile("narration.wav")} />}
      <div
        style={{
          position: "absolute",
          left: 75,
          top: 25,
          fontSize: 23,
          color: C.teal,
          letterSpacing: 1,
        }}
      >
        BRIGHT DATA　/　产品体系与商业逻辑
      </div>
      <div
        style={{
          position: "absolute",
          right: 75,
          top: 22,
          display: "flex",
          gap: 12,
        }}
      >
        {["背景", "架构", "产品", "客户", "启发"].map((n, i) => (
          <div
            key={n}
            style={{
              fontSize: 24,
              padding: "7px 25px",
              borderRadius: 30,
              background: i === scene.chapter ? C.teal : "#e7eeeb",
              color: i === scene.chapter ? "white" : C.muted,
            }}
          >
            {n}
          </div>
        ))}
      </div>
      {data.scenes.map((s) => (
        <Sequence
          key={s.id}
          from={s.from}
          durationInFrames={s.durationInFrames}
        >
          <Scene s={s} />
        </Sequence>
      ))}
      <div
        style={{
          position: "absolute",
          left: 80,
          right: 80,
          top: 955,
          height: 92,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          textAlign: "center",
          fontSize: 31,
          lineHeight: 1.5,
          color: C.ink,
          borderTop: "1px solid #d4e1e3",
          padding: "10px 65px",
          background: C.paper,
        }}
      >
        {reviewing ? (
          <div
            style={{
              width: "100%",
              position: "relative",
              fontSize: 30,
              color: C.navy,
            }}
          >
            <span style={{ fontSize: 22, color: C.teal, marginRight: 24 }}>
              回看要点
            </span>
            {scene.recap}
            <div
              style={{
                position: "absolute",
                left: 0,
                bottom: -17,
                height: 3,
                width: `${holdProgress * 100}%`,
                background: C.teal,
              }}
            />
          </div>
        ) : (
          caption?.text || ""
        )}
      </div>
      <div
        style={{
          position: "absolute",
          left: 0,
          bottom: 0,
          height: 5,
          width: `${(100 * scene.start) / data.duration}%`,
          background: C.teal,
        }}
      />
    </AbsoluteFill>
  );
}
