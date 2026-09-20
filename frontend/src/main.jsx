import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Checkbox,
  Col,
  ConfigProvider,
  Drawer,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Popconfirm,
  Progress,
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
  CreditCard,
  Eraser,
  History,
  KeyRound,
  LogOut,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings,
  ShieldCheck,
  Trash2,
  Users,
} from "lucide-react";
import { api } from "./api";
import "./styles.css";

const { Header, Sider, Content } = Layout;
const { Title, Text } = Typography;

function AuthScreen({ installed, onAuthed }) {
  const [loading, setLoading] = useState(false);
  const [register, setRegister] = useState(false);
  const [captcha, setCaptcha] = useState(null);
  const [captchaLoading, setCaptchaLoading] = useState(false);
  const [form] = Form.useForm();
  const title = installed ? (register ? "注册账号" : "账号登录") : "初始化安装";

  async function loadCaptcha() {
    setCaptchaLoading(true);
    try {
      setCaptcha((await api.get("/auth/captcha")).data);
      form.setFieldValue("captcha_code", "");
    } catch (err) {
      setCaptcha(null);
      message.error(err.response?.data?.detail || "验证码加载失败");
    } finally {
      setCaptchaLoading(false);
    }
  }

  useEffect(() => {
    if (installed && !register) loadCaptcha();
    else setCaptcha(null);
  }, [installed, register]);

  async function submit(values) {
    setLoading(true);
    const isLogin = installed && !register;
    try {
      const url = installed ? (register ? "/auth/register" : "/auth/web-login") : "/install";
      const payload = isLogin ? { ...values, captcha_id: captcha?.captcha_id } : values;
      const { data } = await api.post(url, payload);
      localStorage.setItem("budmon_token", data.token);
      onAuthed();
    } catch (err) {
      message.error(err.response?.data?.detail || "操作失败");
      if (isLogin) loadCaptcha();
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
          <Form form={form} layout="vertical" onFinish={submit}>
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
              <Input size="large" autoComplete="username" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, min: installed && !register ? 1 : 8 }]}>
              <Input.Password size="large" autoComplete={installed ? "current-password" : "new-password"} />
            </Form.Item>
            {installed && !register && <Form.Item name="captcha_code" label="验证码" rules={[{ required: true, message: "请输入验证码" }]}>
              <div className="captcha-row">
                <Input size="large" maxLength={5} autoComplete="off" placeholder="请输入图中字符" />
                <button type="button" className="captcha-image" onClick={loadCaptcha} aria-label="刷新验证码" title="点击刷新验证码">
                  {captchaLoading ? <Spin size="small" /> : captcha?.image ? <img src={captcha.image} alt="验证码" /> : <RefreshCw size={18} />}
                </button>
              </div>
            </Form.Item>}
            <Button type="primary" htmlType="submit" size="large" block loading={loading} disabled={installed && !register && !captcha}>
              {installed ? (register ? "注册" : "登录") : "完成初始化"}
            </Button>
          </Form>
          {installed && <Button type="link" onClick={() => { setRegister(!register); form.resetFields(); }}>
            {register ? "已有账号？去登录" : "创建新账号"}
          </Button>}
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
        <Form.Item name="new_password" label="新密码" rules={[{ required: true, min: 8 }]}>
          <Input.Password />
        </Form.Item>
        <Button type="primary" htmlType="submit" icon={<KeyRound size={16} />}>重置密码</Button>
      </Form>
    </Card>
  );
}

function ActivityRecords({ userId = null }) {
  const [kind, setKind] = useState("purchases");
  const [page, setPage] = useState(1);
  const [result, setResult] = useState({ items: [], total: 0 });
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    api.get(`/admin/${kind}`, { params: { user_id: userId, offset: (page - 1) * 50, limit: 50 } })
      .then(({ data }) => { if (active) setResult(data); })
      .catch(error => { if (active) message.error(error.response?.data?.detail || "加载记录失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [kind, page, userId, refreshKey]);

  const credited = result.items.filter(item => item.status === "credited").length;
  const revoked = result.items.filter(item => item.status === "revoked").length;
  const columns = kind === "purchases" ? [
    {
      title: "用户",
      dataIndex: "username",
      width: 150,
      render: (value, row) => <Space>
        <Avatar size={32}>{(value || "?").slice(0, 1).toUpperCase()}</Avatar>
        <div><div className="cell-title">{value}</div><Text type="secondary">ID {row.user_id || "—"}</Text></div>
      </Space>,
    },
    {
      title: "订单信息",
      width: 300,
      render: (_, row) => <div>
        <Space size={6}><CreditCard size={15} /><span className="cell-title">{row.product_id}</span></Space>
        <div><Text className="transaction-id" type="secondary" copyable={{ text: row.transaction_id }}>{row.transaction_id}</Text></div>
      </div>,
    },
    {
      title: "权益变更",
      width: 110,
      align: "center",
      render: (_, row) => <div className={row.status === "credited" ? "quota-change positive" : "quota-change negative"}>
        {row.status === "credited" ? "+" : "−"}{row.quantity}
        <small>监测名额</small>
      </div>,
    },
    {
      title: "支付金额",
      width: 140,
      render: (_, row) => row.price_milli == null
        ? <Text type="secondary">Apple 未提供</Text>
        : <span className="cell-title">{row.currency || ""} {(row.price_milli / 1000).toFixed(2)}</span>,
    },
    {
      title: "状态",
      width: 150,
      render: (_, row) => <Space direction="vertical" size={4}>
        <Tag color={row.status === "credited" ? "success" : "error"}>{row.status === "credited" ? "已到账" : "已退款 / 撤销"}</Tag>
        <Tag color={row.environment === "Sandbox" ? "gold" : "blue"}>{row.environment === "Sandbox" ? "Sandbox 测试" : "Production"}</Tag>
      </Space>,
    },
    {
      title: "时间",
      width: 210,
      render: (_, row) => <div className="time-stack">
        <span><Text type="secondary">购买</Text>{row.purchased_at}</span>
        <span><Text type="secondary">入账</Text>{row.created_at}</span>
      </div>,
    },
  ] : [
    {
      title: "用户",
      dataIndex: "username",
      render: value => <Space><Avatar size={32}>{(value || "?").slice(0, 1).toUpperCase()}</Avatar><span className="cell-title">{value}</span></Space>,
    },
    { title: "活动", render: (_, row) => <Tag color={row.kind === "register" ? "cyan" : "blue"}>{row.kind === "register" ? "注册并登录" : "账号登录"}</Tag> },
    { title: "发生时间", dataIndex: "created_at", render: value => <span className="cell-title">{value}</span> },
  ];
  return <div className="records-panel">
    <div className="panel-toolbar">
      <Tabs activeKey={kind} onChange={value => { setKind(value); setPage(1); setResult({ items: [], total: 0 }); }} items={[
        { key: "purchases", label: <Space size={6}><CreditCard size={16} />购买记录</Space> },
        { key: "logins", label: <Space size={6}><History size={16} />登录记录</Space> },
      ]} />
      <Button icon={<RefreshCw size={15} />} loading={loading} onClick={() => setRefreshKey(value => value + 1)}>刷新</Button>
    </div>
    <Row gutter={[12, 12]} className="record-stats">
      <Col xs={24} sm={8}><div className="mini-stat"><span>记录总数</span><strong>{result.total}</strong></div></Col>
      {kind === "purchases" && <>
        <Col xs={12} sm={8}><div className="mini-stat success"><span>本页已到账</span><strong>{credited}</strong></div></Col>
        <Col xs={12} sm={8}><div className="mini-stat danger"><span>本页已撤销</span><strong>{revoked}</strong></div></Col>
      </>}
    </Row>
    <div className="table-note"><Text type="secondary">所有时间均为北京时间；Sandbox 订单仅用于测试。</Text></div>
    <Table className="admin-table" rowKey="id" loading={loading} dataSource={result.items} columns={columns}
      scroll={{ x: kind === "purchases" ? 1060 : 640 }}
      locale={{ emptyText: kind === "purchases" ? "暂无购买记录" : "暂无登录记录" }}
      pagination={{ current: page, pageSize: 50, total: result.total, showSizeChanger: false, showTotal: total => `共 ${total} 条`, onChange: setPage }} />
  </div>;
}

function UsersPanel() {
  const [rows, setRows] = useState([]);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [editing, setEditing] = useState(null);
  const [recordsUser, setRecordsUser] = useState(null);
  const [form] = Form.useForm();
  async function load() {
    setLoading(true);
    try { setRows((await api.get(`/admin/users?offset=${page * 100}`)).data); }
    catch (error) { message.error(error.response?.data?.detail || "加载用户失败"); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, [page]);
  async function save(values) {
    setSaving(true);
    try {
      await api.put(`/admin/users/${editing.id}`, { ...values, quota_override: values.quota_override ?? null, plan_expires_at: values.plan_expires_at || null });
      setEditing(null); message.success("用户权益已更新"); load();
    } catch (error) { message.error(error.response?.data?.detail || "更新失败，请检查到期时间格式"); }
    finally { setSaving(false); }
  }
  function edit(row) {
    setEditing(row);
    form.setFieldsValue({
      ...row,
      plan_expires_at: row.plan_expires_at ? row.plan_expires_at.replace(" ", "T") + "Z" : "",
    });
  }
  const filteredRows = rows.filter(row => row.username.toLowerCase().includes(keyword.trim().toLowerCase()));
  const enabledUsers = rows.filter(row => !row.disabled).length;
  const purchasedQuota = rows.reduce((sum, row) => sum + Number(row.purchased_quota || 0), 0);
  const usedQuota = rows.reduce((sum, row) => sum + Number(row.target_used || 0), 0);
  const totalQuota = rows.reduce((sum, row) => sum + Number(row.target_limit || 0), 0);

  return <div className="admin-page">
    <div className="page-heading">
      <div>
        <Title level={3}>用户权益管理</Title>
        <Text type="secondary">查看套餐与配额使用情况，并调整用户权益和账号状态。</Text>
      </div>
      <Button icon={<RefreshCw size={16} />} loading={loading} onClick={load}>刷新数据</Button>
    </div>
    <Row gutter={[14, 14]} className="summary-grid">
      <Col xs={12} lg={6}><Card className="summary-card"><Statistic title="本页用户" value={rows.length} prefix={<Users size={19} />} /></Card></Col>
      <Col xs={12} lg={6}><Card className="summary-card"><Statistic title="正常账号" value={enabledUsers} prefix={<ShieldCheck size={19} />} /></Card></Col>
      <Col xs={12} lg={6}><Card className="summary-card"><Statistic title="累计已购名额" value={purchasedQuota} prefix={<CreditCard size={19} />} /></Card></Col>
      <Col xs={12} lg={6}><Card className="summary-card"><Statistic title="配额使用" value={usedQuota} suffix={`/ ${totalQuota}`} /></Card></Col>
    </Row>
    <Modal title={`${recordsUser?.username || ""} · 活动记录`} open={!!recordsUser} onCancel={()=>setRecordsUser(null)} footer={null} width={1120} destroyOnClose>
      {recordsUser && <ActivityRecords key={recordsUser.id} userId={recordsUser.id} />}
    </Modal>
    <Card className="management-card">
      <Alert type="info" showIcon message="权益计算规则"
        description="套餐或自定义基础名额，加上 iOS 已购名额即为最终配额。套餐到期后自动回落免费版；退款会扣回对应名额。" />
      <div className="list-toolbar">
        <Input allowClear prefix={<Users size={16} />} value={keyword} onChange={event => setKeyword(event.target.value)}
          placeholder="搜索当前页账号" className="user-search" />
        <Text type="secondary">显示 {filteredRows.length} 位用户 · 第 {page + 1} 页</Text>
      </div>
      <Table className="admin-table users-table" rowKey="id" loading={loading} dataSource={filteredRows} pagination={false} scroll={{ x: 1040 }} columns={[
        {
          title: "账号",
          width: 210,
          render: (_, row) => <Space>
            <Avatar>{row.username.slice(0, 1).toUpperCase()}</Avatar>
            <div>
              <div className="cell-title">{row.username}</div>
              <Space size={4}>
                <Tag bordered={false} color={row.role === "admin" ? "purple" : "default"}>{row.role === "admin" ? "管理员" : "用户"}</Tag>
                <Tag bordered={false} color={row.disabled ? "error" : "success"}>{row.disabled ? "已停用" : "正常"}</Tag>
              </Space>
            </div>
          </Space>,
        },
        {
          title: "当前权益",
          width: 190,
          render: (_, row) => <div>
            <Space><Tag color={row.plan_id === "pro" ? "blue" : "default"}>{row.plan_name}</Tag>{row.purchased_quota > 0 && <Tag color="cyan">已购 +{row.purchased_quota}</Tag>}</Space>
            <div className="sub-line">{row.plan_expires_at ? `到期：${row.plan_expires_at}` : "长期有效"}</div>
          </div>,
        },
        {
          title: "配额使用",
          width: 220,
          render: (_, row) => {
            const percent = row.target_limit ? Math.min(100, Math.round(row.target_used / row.target_limit * 100)) : 0;
            return <div className="quota-progress">
              <div><strong>{row.target_used}</strong><span> / {row.target_limit} 个监测目标</span></div>
              <Progress percent={percent} size="small" showInfo={false} status={percent >= 100 ? "exception" : "normal"} />
            </div>;
          },
        },
        {
          title: "账号活动",
          width: 220,
          render: (_, row) => <div className="time-stack">
            <span><Text type="secondary">注册</Text>{row.created_at}</span>
            <span><Text type="secondary">登录</Text>{row.last_login_at || "暂无记录"}</span>
          </div>,
        },
        {
          title: "操作",
          fixed: "right",
          width: 190,
          render: (_, row) => <Space>
            <Button onClick={() => setRecordsUser(row)}>活动记录</Button>
            <Button type="primary" onClick={() => edit(row)}>管理权益</Button>
          </Space>,
        },
      ]} />
      <div className="pager">
        <Button disabled={page === 0 || loading} onClick={()=>setPage(page-1)}>上一页</Button>
        <Text>第 {page+1} 页</Text>
        <Button disabled={rows.length < 100 || loading} onClick={()=>setPage(page+1)}>下一页</Button>
      </div>
    </Card>
    <Drawer title="管理用户权益" open={!!editing} onClose={()=>setEditing(null)} width={480} destroyOnClose
      extra={<Button type="primary" loading={saving} onClick={() => form.submit()}>保存更改</Button>}>
      {editing && <div className="entitlement-drawer">
        <div className="drawer-user">
          <Avatar size={46}>{editing.username.slice(0, 1).toUpperCase()}</Avatar>
          <div><Title level={5}>{editing.username}</Title><Text type="secondary">当前可用 {editing.target_limit} 个名额，已使用 {editing.target_used} 个</Text></div>
        </div>
        <Form form={form} layout="vertical" onFinish={save}>
          <div className="form-section-title">套餐与基础名额</div>
          <Form.Item name="plan_id" label="用户套餐" extra="套餐决定默认的基础名额。">
            <Select size="large" options={[{value:"free",label:"免费版 · 5 个基础名额"},{value:"pro",label:"专业版 · 30 个基础名额"}]} />
          </Form.Item>
          <Form.Item name="quota_override" label="自定义基础名额" extra="留空则使用套餐名额；已购买名额始终在此基础上累加。">
            <InputNumber size="large" min={0} max={10000} placeholder="使用套餐默认值" className="full" />
          </Form.Item>
          <Form.Item name="plan_expires_at" label="套餐到期时间" extra="留空表示长期有效；填写时必须包含时区。">
            <Input size="large" placeholder="例如 2027-01-01T00:00:00+08:00" />
          </Form.Item>
          <div className="form-section-title">账号状态</div>
          <div className="status-control">
            <div><div className="cell-title">停用此账号</div><Text type="secondary">停用后立即清除登录会话和推送设备。</Text></div>
            <Form.Item name="disabled" valuePropName="checked" noStyle><Switch /></Form.Item>
          </div>
        </Form>
      </div>}
    </Drawer>
  </div>;
}

function Shell({ onLogout }) {
  const [selected, setSelected] = useState("dashboard");
  const [profile, setProfile] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => { api.get("/me").then(({data}) => setProfile(data)).catch(() => {}); }, [refreshKey]);
  const items = useMemo(() => [
    { key: "dashboard", icon: <Activity size={18} />, label: "运行状态" },
    { key: "targets", icon: <Bell size={18} />, label: "监控目标" },
    ...(profile?.role === "admin" ? [
      { key: "settings", icon: <Settings size={18} />, label: "系统配置" },
      { key: "users", icon: <Users size={18} />, label: "权益管理" },
      { key: "records", icon: <CreditCard size={18} />, label: "购买记录" },
    ] : []),
    { key: "password", icon: <KeyRound size={18} />, label: "重置密码" },
  ], [profile]);

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
            {profile && <Text>{profile.username} · {profile.plan_name} · {profile.target_used}/{profile.target_limit}</Text>}
            <Button icon={<Play size={16} />} onClick={runNow}>立即检测</Button>
            <Button icon={<RefreshCw size={16} />} onClick={() => setRefreshKey((v) => v + 1)}>刷新</Button>
            <Button icon={<LogOut size={16} />} onClick={onLogout}>退出</Button>
          </Space>
        </Header>
        <Content className="content">
          {selected === "dashboard" && <Dashboard refreshKey={refreshKey} onChanged={() => setRefreshKey((v) => v + 1)} />}
          {selected === "targets" && <Targets onChanged={() => setRefreshKey((v) => v + 1)} />}
          {selected === "settings" && profile?.role === "admin" && <SettingsPanel />}
          {selected === "users" && profile?.role === "admin" && <UsersPanel />}
          {selected === "records" && profile?.role === "admin" && <div className="admin-page">
            <div className="page-heading">
              <div>
                <Title level={3}>购买与账号活动</Title>
                <Text type="secondary">核对 Apple 订单状态、名额到账情况及用户登录记录。</Text>
              </div>
            </div>
            <Card className="management-card"><ActivityRecords /></Card>
          </div>}
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
  return <Shell onLogout={async () => { try { await api.post("/auth/logout"); localStorage.removeItem("budmon_token"); setAuthed(false); } catch { message.error("退出失败，请重试"); } }} />;
}

createRoot(document.getElementById("root")).render(
  <ConfigProvider theme={{ token: { borderRadius: 6, colorPrimary: "#1677ff" } }}>
    <App />
  </ConfigProvider>
);
