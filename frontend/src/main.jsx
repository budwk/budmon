import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  ConfigProvider,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  Activity,
  Bell,
  Eraser,
  KeyRound,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings,
  Trash2,
} from "lucide-react";
import { api } from "./api";
import "./styles.css";

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

function AuthScreen({ installed, onAuthed }) {
  const [loading, setLoading] = useState(false);
  const title = installed ? "管理员登录" : "初始化安装";

  async function submit(values) {
    setLoading(true);
    try {
      const url = installed ? "/auth/login" : "/install";
      const { data } = await api.post(url, values);
      localStorage.setItem("budmon_token", data.token);
      onAuthed();
    } catch (err) {
      message.error(err.response?.data?.detail || "操作失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <Card className="auth-card">
        <Space direction="vertical" size={20} className="full">
          <div>
            <Title level={2}>BudMon</Title>
            <Text type="secondary">{title}</Text>
          </div>
          <Form layout="vertical" onFinish={submit}>
            <Form.Item name="username" label="管理员账号" rules={[{ required: true }]}>
              <Input size="large" autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, min: 6 }]}>
              <Input.Password size="large" autoComplete={installed ? "current-password" : "new-password"} />
            </Form.Item>
            <Button type="primary" htmlType="submit" size="large" block loading={loading}>
              {installed ? "登录" : "完成初始化"}
            </Button>
          </Form>
        </Space>
      </Card>
    </div>
  );
}

function Dashboard({ refreshKey, onChanged }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setData((await api.get("/dashboard")).data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [refreshKey]);

  async function clearLogs() {
    await api.delete("/monitor/logs");
    message.success("监测记录已清空");
    load();
    onChanged();
  }

  if (loading) return <Spin />;
  return (
    <Space direction="vertical" size={18} className="full">
      <Row gutter={16}>
        <Col xs={24} md={8}>
          <Card><Statistic title="监控目标" value={data.total} /></Card>
        </Col>
        <Col xs={24} md={8}>
          <Card><Statistic title="启用中" value={data.enabled} /></Card>
        </Col>
        <Col xs={24} md={8}>
          <Card><Statistic title="故障中" value={data.down} valueStyle={{ color: data.down ? "#cf1322" : "#1677ff" }} /></Card>
        </Col>
      </Row>
      <Card
        title="最近检测记录"
        extra={
          <Popconfirm title="确认清空全部监测记录？" onConfirm={clearLogs}>
            <Button danger icon={<Eraser size={16} />}>清空记录</Button>
          </Popconfirm>
        }
      >
        <Table
          rowKey="id"
          dataSource={data.recent}
          pagination={false}
          columns={[
            { title: "网站", dataIndex: "target_name" },
            { title: "类型", dataIndex: "event_type", render: (v) => (v === "certificate" ? <Tag color="gold">证书</Tag> : <Tag color="blue">服务</Tag>) },
            { title: "结果", dataIndex: "ok", render: (v) => (v ? <Tag color="green">正常</Tag> : <Tag color="red">失败</Tag>) },
            { title: "状态码", dataIndex: "status_code", render: (v) => v || "-" },
            { title: "证书剩余", dataIndex: "cert_days", render: (v) => (v === null || v === undefined ? "-" : `${v} 天`) },
            { title: "错误", dataIndex: "error", ellipsis: true, render: (v) => v || "-" },
            { title: "时间", dataIndex: "checked_at" },
          ]}
        />
      </Card>
    </Space>
  );
}

function Targets({ onChanged }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null);
  const [form] = Form.useForm();

  async function load() {
    setLoading(true);
    try {
      setRows((await api.get("/targets")).data);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  function open(row) {
    setEditing(row || {});
    form.setFieldsValue(row || { enabled: true });
  }

  async function save(values) {
    try {
      if (editing.id) await api.put(`/targets/${editing.id}`, values);
      else await api.post("/targets", values);
      message.success("已保存");
      setEditing(null);
      load();
      onChanged();
    } catch (err) {
      message.error(err.response?.data?.detail || "保存失败");
    }
  }

  async function remove(id) {
    await api.delete(`/targets/${id}`);
    message.success("已删除");
    load();
    onChanged();
  }

  return (
    <Card
      title="监控目标"
      extra={<Button type="primary" icon={<Plus size={16} />} onClick={() => open(null)}>新增</Button>}
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rows}
        columns={[
          { title: "网站名称", dataIndex: "name" },
          { title: "网站地址", dataIndex: "url", ellipsis: true },
          { title: "启用", dataIndex: "enabled", render: (v) => (v ? <Tag color="blue">启用</Tag> : <Tag>停用</Tag>) },
          { title: "状态", dataIndex: "last_status", render: (v) => <Tag color={v === "up" ? "green" : v === "down" ? "red" : "default"}>{v}</Tag> },
          { title: "连续失败", dataIndex: "failure_count" },
          { title: "证书剩余", dataIndex: "last_cert_days", render: (v) => (v === null || v === undefined ? "-" : `${v} 天`) },
          { title: "最后检测", dataIndex: "last_checked_at", render: (v) => v || "-" },
          {
            title: "操作",
            render: (_, row) => (
              <Space>
                <Button size="small" onClick={() => open(row)}>编辑</Button>
                <Popconfirm title="确认删除？" onConfirm={() => remove(row.id)}>
                  <Button size="small" danger icon={<Trash2 size={14} />} />
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal title={editing?.id ? "编辑目标" : "新增目标"} open={!!editing} onCancel={() => setEditing(null)} footer={null}>
        <Form layout="vertical" form={form} onFinish={save}>
          <Form.Item name="name" label="网站名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="url" label="网站地址" rules={[{ required: true, type: "url" }]}>
            <Input placeholder="https://example.com" />
          </Form.Item>
          <Form.Item name="enabled" label="是否启用" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<Save size={16} />}>保存</Button>
        </Form>
      </Modal>
    </Card>
  );
}

function SettingsPanel() {
  const [loading, setLoading] = useState(true);
  const [monitorForm] = Form.useForm();
  const [smsForm] = Form.useForm();
  const [emailForm] = Form.useForm();
  const smsProvider = Form.useWatch("provider", smsForm) || "aliyun";

  async function load() {
    setLoading(true);
    try {
      const { data } = await api.get("/settings");
      monitorForm.setFieldsValue(data.monitor);
      smsForm.setFieldsValue(data.sms);
      emailForm.setFieldsValue(data.email);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function saveMonitor(values) {
    await api.put("/settings/monitor", values);
    message.success("监控策略已保存");
  }

  async function saveSms(values) {
    values.templates = {
      ...values.templates,
      service_down: { ...values.templates?.service_down, params: ["name"] },
      cert_expiring: { ...values.templates?.cert_expiring, params: ["name", "day"] },
    };
    await api.put("/settings/sms", values);
    message.success("短信配置已保存");
  }

  async function saveEmail(values) {
    await api.put("/settings/email", values);
    message.success("邮箱配置已保存");
  }

  if (loading) return <Spin />;
  return (
    <Tabs
      items={[
        {
          key: "monitor",
          label: "监控策略",
          children: (
            <Card>
              <Form layout="vertical" form={monitorForm} onFinish={saveMonitor}>
                <Row gutter={16}>
                  <Col xs={24} md={12}>
                    <Form.Item name="interval_seconds" label="定时任务间隔时间（秒）" rules={[{ required: true }]}>
                      <InputNumber min={10} max={86400} className="full" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="retry_delay_seconds" label="访问失败重试间隔（秒）" rules={[{ required: true }]}>
                      <InputNumber min={0} max={300} className="full" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="cert_expire_days" label="证书过期告警阈值（天）" rules={[{ required: true }]}>
                      <InputNumber min={1} max={365} className="full" />
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item name="notify_methods" label="告警通知方式">
                  <Checkbox.Group options={[{ label: "短信", value: "sms" }, { label: "邮箱", value: "email" }]} />
                </Form.Item>
                <Row gutter={16}>
                  <Col xs={24} md={12}>
                    <Form.Item name="sms_targets" label="短信通知目标">
                      <Input.TextArea rows={6} placeholder="每行输入一个手机号" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="email_targets" label="邮箱通知目标">
                      <Input.TextArea rows={6} placeholder="每行输入一个邮箱地址" />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit" icon={<Save size={16} />}>保存策略</Button>
              </Form>
            </Card>
          ),
        },
        {
          key: "sms",
          label: "短信渠道及模版",
          children: (
            <Card>
              <Alert
                type="info"
                showIcon
                message="短信模板可维护。服务通知变量：name；证书过期变量：name、day。阿里云模板变量按 JSON 对象发送，腾讯云模板变量按模板参数顺序发送。"
                className="mb16"
              />
              <Form layout="vertical" form={smsForm} onFinish={saveSms}>
                <Form.Item name="provider" label="短信渠道">
                  <Select
                    placeholder="请选择短信渠道"
                    options={[
                      { label: "阿里云短信", value: "aliyun" },
                      { label: "腾讯云短信", value: "tencent" },
                    ]}
                  />
                </Form.Item>
                {smsProvider === "aliyun" && (
                  <>
                    <Title level={5}>阿里云短信</Title>
                    <Row gutter={16}>
                      <Col xs={24} md={12}>
                        <Form.Item name={["aliyun", "accessKeyId"]} label="accessKeyId"><Input /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["aliyun", "accessKeySecret"]} label="accessKeySecret"><Input.Password /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["aliyun", "regionId"]} label="regionId"><Input /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["aliyun", "signName"]} label="signName"><Input /></Form.Item>
                      </Col>
                    </Row>
                  </>
                )}
                {smsProvider === "tencent" && (
                  <>
                    <Title level={5}>腾讯云短信</Title>
                    <Row gutter={16}>
                      <Col xs={24} md={12}>
                        <Form.Item name={["tencent", "secretId"]} label="secretId"><Input /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["tencent", "secretKey"]} label="secretKey"><Input.Password /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["tencent", "region"]} label="region"><Input /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["tencent", "smsSdkAppId"]} label="smsSdkAppId"><Input /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name={["tencent", "signName"]} label="signName"><Input /></Form.Item>
                      </Col>
                    </Row>
                  </>
                )}
                <Title level={5}>短信模板</Title>
                <Row gutter={16}>
                  <Col xs={24} md={12}>
                    <Form.Item name={["templates", "service_down", "name"]} label="服务通知名称">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name={["templates", "service_down", "code"]} label="服务通知模板 Code">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col xs={24}>
                    <Form.Item name={["templates", "service_down", "content"]} label="服务通知模板内容">
                      <Input.TextArea rows={2} />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name={["templates", "cert_expiring", "name"]} label="证书过期名称">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name={["templates", "cert_expiring", "code"]} label="证书过期模板 Code">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col xs={24}>
                    <Form.Item name={["templates", "cert_expiring", "content"]} label="证书过期模板内容">
                      <Input.TextArea rows={2} />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit" icon={<Save size={16} />}>保存短信配置</Button>
              </Form>
            </Card>
          ),
        },
        {
          key: "email",
          label: "邮箱渠道",
          children: (
            <Card>
              <Form layout="vertical" form={emailForm} onFinish={saveEmail}>
                <Row gutter={16}>
                  <Col xs={24} md={12}>
                    <Form.Item name="host" label="SMTP Host"><Input placeholder="smtp.example.com" /></Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="port" label="SMTP SSL Port"><InputNumber min={1} max={65535} className="full" /></Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="username" label="SMTP Username"><Input /></Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="password" label="SMTP Password"><Input.Password /></Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item name="sender" label="发件人"><Input placeholder="为空时使用 SMTP Username" /></Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit" icon={<Save size={16} />}>保存邮箱配置</Button>
              </Form>
            </Card>
          ),
        },
      ]}
    />
  );
}

function PasswordPanel() {
  async function save(values) {
    await api.post("/auth/reset-password", values);
    message.success("密码已重置，请重新登录");
    localStorage.removeItem("budmon_token");
    window.location.reload();
  }
  return (
    <Card title="重置密码">
      <Form layout="vertical" onFinish={save} className="narrow">
        <Form.Item name="old_password" label="原密码" rules={[{ required: true }]}>
          <Input.Password />
        </Form.Item>
        <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 6 }]}>
          <Input.Password />
        </Form.Item>
        <Button type="primary" htmlType="submit" icon={<KeyRound size={16} />}>重置密码</Button>
      </Form>
    </Card>
  );
}

function Shell({ onLogout }) {
  const [selected, setSelected] = useState("dashboard");
  const [refreshKey, setRefreshKey] = useState(0);
  const items = useMemo(() => [
    { key: "dashboard", icon: <Activity size={18} />, label: "运行状态" },
    { key: "targets", icon: <Bell size={18} />, label: "监控目标" },
    { key: "settings", icon: <Settings size={18} />, label: "系统配置" },
    { key: "password", icon: <KeyRound size={18} />, label: "重置密码" },
  ], []);

  async function runNow() {
    const hide = message.loading("正在检测...", 0);
    try {
      await api.post("/monitor/run");
      message.success("检测完成");
      setRefreshKey((v) => v + 1);
    } catch (err) {
      message.error(err.response?.data?.detail || "检测失败");
    } finally {
      hide();
    }
  }

  return (
    <Layout className="app-shell">
      <Sider breakpoint="lg" collapsedWidth="0">
        <div className="brand">BudMon</div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={items} onClick={({ key }) => setSelected(key)} />
      </Sider>
      <Layout>
        <Header className="topbar">
          <Space>
            <Button icon={<Play size={16} />} onClick={runNow}>立即检测</Button>
            <Button icon={<RefreshCw size={16} />} onClick={() => setRefreshKey((v) => v + 1)}>刷新</Button>
            <Button icon={<LogOut size={16} />} onClick={onLogout}>退出</Button>
          </Space>
        </Header>
        <Content className="content">
          {selected === "dashboard" && <Dashboard refreshKey={refreshKey} onChanged={() => setRefreshKey((v) => v + 1)} />}
          {selected === "targets" && <Targets onChanged={() => setRefreshKey((v) => v + 1)} />}
          {selected === "settings" && <SettingsPanel />}
          {selected === "password" && <PasswordPanel />}
        </Content>
      </Layout>
    </Layout>
  );
}

function App() {
  const [booting, setBooting] = useState(true);
  const [installed, setInstalled] = useState(false);
  const [authed, setAuthed] = useState(!!localStorage.getItem("budmon_token"));

  async function boot() {
    const { data } = await api.get("/install/status");
    setInstalled(data.installed);
    setBooting(false);
  }

  useEffect(() => {
    boot();
    window.addEventListener("budmon-auth-expired", () => setAuthed(false));
  }, []);

  if (booting) return <div className="center"><Spin /></div>;
  if (!authed) return <AuthScreen installed={installed} onAuthed={() => { setAuthed(true); boot(); }} />;
  return <Shell onLogout={() => { localStorage.removeItem("budmon_token"); setAuthed(false); }} />;
}

createRoot(document.getElementById("root")).render(
  <ConfigProvider theme={{ token: { borderRadius: 6, colorPrimary: "#1677ff" } }}>
    <App />
  </ConfigProvider>
);
