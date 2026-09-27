import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { renderAsync } from "docx-preview";
import {
  App,
  Alert,
  Button,
  Card,
  Col,
  ConfigProvider,
  Descriptions,
  Divider,
  Empty,
  Flex,
  Form,
  Input,
  InputNumber,
  Layout,
  List,
  Modal,
  Popconfirm,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
} from "antd";
import {
  CloudUploadOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  FileTextOutlined,
  PlusOutlined,
  ReloadOutlined,
  SettingOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import "./styles.css";

const { Header, Content, Sider } = Layout;
const { Paragraph, Text, Title } = Typography;

const statusText = {
  extracting: "正在解压",
  queued: "等待批改",
  grading: "批改中",
  completed: "已完成",
  completed_with_errors: "部分失败",
  failed: "处理失败",
  reviewed: "已复核",
};

function fileType(filename = "") {
  const suffix = filename.toLowerCase().split(".").pop();
  if (suffix === "docx") return "docx";
  if (suffix === "pdf") return "pdf";
  if (["jpg", "jpeg", "png", "gif", "webp"].includes(suffix)) return "image";
  if (["txt", "md"].includes(suffix)) return "text";
  return "unknown";
}

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error("无法连接本地服务，请确认后端仍在运行");
  }
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : { detail: await response.text() };
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
}

function statusColor(status) {
  if (status === "completed" || status === "reviewed") return "success";
  if (status === "failed" || status === "completed_with_errors") return "error";
  if (status === "grading" || status === "extracting") return "processing";
  return "default";
}

function AppShell() {
  const { message } = App.useApp();
  const [rubrics, setRubrics] = useState([]);
  const [batches, setBatches] = useState([]);
  const [selectedRubric, setSelectedRubric] = useState(null);
  const [selectedBatch, setSelectedBatch] = useState(null);
  const [modelSettings, setModelSettings] = useState(null);
  const [rubricOpen, setRubricOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTesting, setSettingsTesting] = useState(false);
  const [previewTarget, setPreviewTarget] = useState(null);
  const [uploadStage, setUploadStage] = useState("");
  const [rubricForm] = Form.useForm();
  const [batchForm] = Form.useForm();
  const [settingsForm] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const selectedBatchId = useRef(null);

  async function loadAll() {
    const [nextRubrics, nextBatches, nextSettings] = await Promise.all([
      request("/api/rubrics"),
      request("/api/batches"),
      request("/api/settings/model"),
    ]);
    setRubrics(nextRubrics);
    setBatches(nextBatches);
    setModelSettings(nextSettings);
    setSelectedRubric((current) => {
      const next = current && nextRubrics.some((rubric) => rubric.id === current)
        ? current
        : nextRubrics[0]?.id || null;
      if (!batchForm.getFieldValue("rubric_id")) {
        batchForm.setFieldValue("rubric_id", next);
      }
      return next;
    });
  }

  async function openBatch(batchId) {
    selectedBatchId.current = batchId;
    setSelectedBatch(await request(`/api/batches/${batchId}`));
  }

  useEffect(() => {
    let disposed = false;
    async function refresh() {
      try {
        await loadAll();
        if (!disposed && selectedBatchId.current) {
          await openBatch(selectedBatchId.current);
        }
      } catch (error) {
        if (!disposed) message.error(error.message);
      }
    }
    refresh();
    const timer = setInterval(() => refresh(), 3000);
    return () => clearInterval(timer);
  }, []);

  async function openSettings() {
    try {
      const settings = await request("/api/settings/model");
      setModelSettings(settings);
      settingsForm.setFieldsValue({
        model: settings.model,
        api_base: settings.api_base,
        concurrency: settings.concurrency,
        api_key: "",
      });
      setSettingsOpen(true);
    } catch (error) {
      message.error(error.message);
    }
  }

  async function saveSettings(values) {
    try {
      const settings = await request("/api/settings/model", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...values,
          api_key: values.api_key || null,
        }),
      });
      setModelSettings(settings);
      setSettingsOpen(false);
      message.success("模型配置已保存");
    } catch (error) {
      message.error(error.message);
    }
  }

  async function testSettings() {
    setSettingsTesting(true);
    try {
      const values = await settingsForm.validateFields();
      await request("/api/settings/model", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...values, api_key: values.api_key || null }),
      });
      const result = await request("/api/settings/model/test", { method: "POST" });
      message.success(`模型连接成功：${result.response || "已收到响应"}`);
      await loadAll();
    } catch (error) {
      message.error(`模型连接失败：${error.message}`);
    } finally {
      setSettingsTesting(false);
    }
  }

  async function saveRubric(values) {
    try {
      const ruleFile = values.rule_file?.[0]?.originFileObj;
      if (!ruleFile) {
        message.warning("请选择评分规则文件");
        return;
      }
      const formData = new FormData();
      formData.append("name", values.name);
      formData.append("description", values.description || "");
      formData.append("rule_file", ruleFile);
      const rubric = await request("/api/rubrics", {
        method: "POST",
        body: formData,
      });
      setRubricOpen(false);
      rubricForm.resetFields();
      setSelectedRubric(rubric.id);
      batchForm.setFieldValue("rubric_id", rubric.id);
      await loadAll();
      message.success("评分标准已保存");
    } catch (error) {
      message.error(error.message);
    }
  }

  async function createBatch(values) {
    const archive = values.archive?.[0]?.originFileObj;
    if (!archive) {
      message.warning("请先选择 ZIP 文件");
      return;
    }
    setLoading(true);
    setUploadStage("正在上传并读取压缩包，完成后会自动进入批改队列……");
    try {
      const formData = new FormData();
      formData.append("name", values.name);
      formData.append("rubric_id", values.rubric_id);
      formData.append("archive", archive);
      const result = await request("/api/batches", { method: "POST", body: formData });
      batchForm.resetFields();
      await loadAll();
      await openBatch(result.id);
      message.success("批次已建立，后台开始异步批改");
      setUploadStage("");
    } catch (error) {
      message.error(error.message);
      setUploadStage(`建立批次失败：${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function deleteBatch(batchId) {
    try {
      await request(`/api/batches/${batchId}`, { method: "DELETE" });
      if (selectedBatchId.current === batchId) {
        selectedBatchId.current = null;
        setSelectedBatch(null);
      }
      await loadAll();
      message.success("批次及其文件已永久删除");
    } catch (error) {
      message.error(error.message);
    }
  }

  function openRubricPreview(rubric) {
    setPreviewTarget({
      title: rubric.name,
      filename: rubric.source_path || "评分标准.txt",
      content: rubric.content,
      url: rubric.source_path ? `/api/rubrics/${rubric.id}/file` : null,
    });
  }

  const batchColumns = [
    {
      title: "批次",
      dataIndex: "name",
      render: (name, batch) => (
        <Button type="link" className="batch-link" onClick={() => openBatch(batch.id)}>
          {name}
        </Button>
      ),
    },
    { title: "评分标准", dataIndex: "rubric_name" },
    {
      title: "进度",
      render: (_, batch) => `${batch.completed}/${batch.total}`,
    },
    {
      title: "状态",
      dataIndex: "status",
      render: (status) => <Tag color={statusColor(status)}>{statusText[status] || status}</Tag>,
    },
    {
      title: "操作",
      fixed: "right",
      render: (_, batch) => <BatchDeleteButton batchId={batch.id} onDelete={deleteBatch} />,
    },
  ];

  return (
    <Layout className="app-layout">
      <Header className="app-header">
        <Flex align="center" justify="space-between" gap={24}>
          <Flex align="center" gap={14}>
            <div className="brand-mark"><FileTextOutlined /></div>
            <div>
              <Text className="brand-kicker">LOCAL GRADING STUDIO</Text>
              <Title level={4} className="brand-title">作业批改工作台</Title>
            </div>
          </Flex>
          <Flex align="center" gap={12}>
            <Tag color={modelSettings?.model ? "success" : "warning"} bordered={false}>
              {modelSettings?.model ? `模型：${modelSettings.model}` : "模型未配置"}
            </Tag>
            <Button type="text" icon={<SettingOutlined />} onClick={openSettings}>模型设置</Button>
          </Flex>
        </Flex>
      </Header>
      <Layout>
        <Sider width={292} className="app-sider">
          <div className="sider-inner">
            <Flex justify="space-between" align="center" className="sider-title">
              <div>
                <Text type="secondary">评分标准</Text>
                <Title level={5}>标准库</Title>
              </div>
              <Button type="primary" shape="circle" icon={<PlusOutlined />} onClick={() => setRubricOpen(true)} />
            </Flex>
            <List
              className="rubric-list"
              dataSource={rubrics}
              locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有评分标准" /> }}
              renderItem={(rubric) => (
                <List.Item
                  className={selectedRubric === rubric.id ? "rubric-item rubric-item-active" : "rubric-item"}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selectedRubric === rubric.id}
                  onClick={() => {
                    setSelectedRubric(rubric.id);
                    batchForm.setFieldValue("rubric_id", rubric.id);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedRubric(rubric.id);
                      batchForm.setFieldValue("rubric_id", rubric.id);
                    }
                  }}
                >
                  <List.Item.Meta
                    title={rubric.name}
                    description={rubric.description || "未填写说明"}
                  />
                  <Button
                    type="text"
                    icon={<EyeOutlined />}
                    aria-label={`查看${rubric.name}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      openRubricPreview(rubric);
                    }}
                  />
                </List.Item>
              )}
            />
            <Card size="small" className="sider-info" bordered={false}>
              <Flex vertical gap={12}>
                <Text strong><SettingOutlined />　处理策略</Text>
                <Descriptions column={1} size="small" colon={false}>
                  <Descriptions.Item label="批改方式">受限异步</Descriptions.Item>
                  <Descriptions.Item label="评判模型">可接入 Jev</Descriptions.Item>
                  <Descriptions.Item label="数据位置">本机目录</Descriptions.Item>
                </Descriptions>
              </Flex>
            </Card>
          </div>
        </Sider>
        <Content className="app-content">
          <div className="content-wrap">
            <div className="page-heading">
              <div>
                <Text className="eyebrow">今日工作台</Text>
                <Title>让批改结果，直接变成教学反馈。</Title>
                <Paragraph type="secondary">选择评分标准，上传全班作业，系统会逐份处理并整理共性问题。</Paragraph>
              </div>
              <Statistic title="批次总数" value={batches.length} suffix="个" />
            </div>

            <Card className="upload-card" bordered={false}>
              <Flex justify="space-between" align="start" className="card-heading">
                <div>
                  <Text className="eyebrow">新建批次</Text>
                  <Title level={4}>上传全班作业</Title>
                </div>
                <Tag icon={<CloudUploadOutlined />}>DOCX 压缩包</Tag>
              </Flex>
              <Form form={batchForm} layout="vertical" onFinish={createBatch}>
                {uploadStage && <Alert type={uploadStage.startsWith("建立批次失败") ? "error" : "info"} showIcon message={uploadStage} style={{ marginBottom: 18 }} />}
                <Row gutter={18}>
                  <Col xs={24} md={12}>
                    <Form.Item label="批次名称" name="name" rules={[{ required: true, message: "请输入批次名称" }]}>
                      <Input size="large" placeholder="例如：高一数学 · 函数单元练习" />
                    </Form.Item>
                  </Col>
                  <Col xs={24} md={12}>
                    <Form.Item label="使用评分标准" name="rubric_id" rules={[{ required: true, message: "请选择评分标准" }]}>
                      <Select
                        size="large"
                        placeholder="选择一套评分标准"
                        onChange={setSelectedRubric}
                      >
                        {rubrics.map((rubric) => <Select.Option key={rubric.id} value={rubric.id}>{rubric.name}</Select.Option>)}
                      </Select>
                    </Form.Item>
                  </Col>
                </Row>
                <Form.Item
                  name="archive"
                  label="作业压缩包"
                  valuePropName="fileList"
                  getValueFromEvent={(event) => event?.fileList || []}
                  rules={[{ required: true, message: "请选择 ZIP 文件" }]}
                >
                  <Upload.Dragger accept=".zip" maxCount={1} beforeUpload={() => false} showUploadList>
                    <p className="ant-upload-drag-icon"><CloudUploadOutlined /></p>
                    <p className="ant-upload-text">点击或拖拽 ZIP 文件到这里</p>
                  <p className="ant-upload-hint">压缩包内请放入每位学生的一份 DOCX 作业</p>
                  </Upload.Dragger>
                </Form.Item>
                <Button type="primary" htmlType="submit" size="large" icon={<UploadOutlined />} loading={loading}>
                  开始建立批次
                </Button>
              </Form>
            </Card>

            <Card className="table-card" bordered={false}>
              <Flex justify="space-between" align="center" className="card-heading">
                <div>
                  <Text className="eyebrow">批改记录</Text>
                  <Title level={4}>最近批次</Title>
                </div>
                <Button icon={<ReloadOutlined />} onClick={loadAll}>刷新</Button>
              </Flex>
              <Table rowKey="id" columns={batchColumns} dataSource={batches} pagination={false} locale={{ emptyText: "上传第一批作业后，处理进度会显示在这里" }} />
            </Card>

            {selectedBatch && (
              <BatchDetail
                batch={selectedBatch}
                onRefresh={() => openBatch(selectedBatch.id)}
                onDelete={deleteBatch}
                onPreview={(item) => setPreviewTarget({
                  title: item.filename,
                  filename: item.filename,
                  url: `/api/submissions/${item.id}/preview`,
                })}
              />
            )}
          </div>
        </Content>
      </Layout>

      <Modal
        title={<div><Text type="secondary">标准库</Text><Title level={4} style={{ margin: "4px 0 0" }}>新建评分标准</Title></div>}
        open={rubricOpen}
        onCancel={() => { setRubricOpen(false); rubricForm.resetFields(); }}
        footer={null}
        destroyOnHidden
      >
        <Form form={rubricForm} layout="vertical" onFinish={saveRubric} className="rubric-form">
          <Form.Item label="名称" name="name" rules={[{ required: true, message: "请输入评分标准名称" }]}>
            <Input placeholder="例如：函数题基础评分标准" />
          </Form.Item>
          <Form.Item label="说明" name="description">
            <Input placeholder="适用年级或题型" />
          </Form.Item>
          <Form.Item
            label="评分规则文件"
            name="rule_file"
            valuePropName="fileList"
            getValueFromEvent={(event) => event?.fileList || []}
            rules={[{ required: true, message: "请选择评分规则文件" }]}
          >
            <Upload beforeUpload={() => false} maxCount={1} accept=".docx,.txt,.md">
              <Button icon={<UploadOutlined />}>选择 DOCX、TXT 或 Markdown</Button>
            </Upload>
          </Form.Item>
          <Flex justify="end" gap={10}>
            <Button onClick={() => { setRubricOpen(false); rubricForm.resetFields(); }}>取消</Button>
            <Button type="primary" htmlType="submit">保存评分标准</Button>
          </Flex>
        </Form>
      </Modal>

      <Modal
        title={<div><Text type="secondary">本地配置</Text><Title level={4} style={{ margin: "4px 0 0" }}>模型设置</Title></div>}
        open={settingsOpen}
        onCancel={() => setSettingsOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Alert
          type={modelSettings?.api_key_configured ? "success" : "warning"}
          showIcon
          message={modelSettings?.api_key_configured ? `当前密钥：${modelSettings.api_key_masked}` : "尚未配置 API 密钥"}
          description="配置只保存在当前电脑，用于通过 LiteLLM 调用模型。"
          style={{ marginBottom: 18 }}
        />
        <Form form={settingsForm} layout="vertical" onFinish={saveSettings} className="rubric-form">
          <Form.Item label="模型名称" name="model" rules={[{ required: true, message: "请输入模型名称" }]}>
            <Input placeholder="例如：openai/gpt-4o-mini" />
          </Form.Item>
          <Form.Item label="API 地址" name="api_base">
            <Input placeholder="可选，例如：https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item label="API 密钥" name="api_key" extra="留空表示继续使用当前已保存的密钥">
            <Input.Password placeholder="输入新密钥" />
          </Form.Item>
          <Form.Item label="并发数" name="concurrency" rules={[{ required: true, message: "请输入并发数" }]}>
            <Input type="number" min={1} max={20} />
          </Form.Item>
          <Flex justify="end" gap={10}>
            <Button onClick={testSettings} loading={settingsTesting}>测试连接</Button>
            <Button type="primary" htmlType="submit">保存配置</Button>
          </Flex>
        </Form>
      </Modal>
      <DocumentPreview target={previewTarget} onClose={() => setPreviewTarget(null)} />
    </Layout>
  );
}

function DocumentPreview({ target, onClose }) {
  const bodyRef = useRef(null);
  const stylesRef = useRef(null);
  const [preview, setPreview] = useState({ loading: false, type: null, source: "", error: "" });

  useEffect(() => {
    if (!target) return undefined;

    let disposed = false;
    let objectUrl = "";
    const type = fileType(target.filename);

    setPreview({ loading: true, type, source: "", error: "" });
    if (bodyRef.current) bodyRef.current.replaceChildren();
    if (stylesRef.current) stylesRef.current.replaceChildren();

    async function loadPreview() {
      try {
        if (type === "text" && target.content != null && !target.url) {
          if (!disposed) setPreview({ loading: false, type, source: target.content, error: "" });
          return;
        }

        const response = await fetch(target.url);
        if (!response.ok) {
          throw new Error("无法读取文件");
        }
        const blob = await response.blob();

        if (type === "docx") {
          if (disposed || !bodyRef.current || !stylesRef.current) return;
          await renderAsync(
            await blob.arrayBuffer(),
            bodyRef.current,
            stylesRef.current,
            {
              breakPages: true,
              renderHeaders: true,
              renderFooters: true,
              renderFootnotes: true,
              renderEndnotes: true,
            },
          );
          if (!disposed) setPreview({ loading: false, type, source: "", error: "" });
          return;
        }

        if (type === "text") {
          const source = await blob.text();
          if (!disposed) setPreview({ loading: false, type, source, error: "" });
          return;
        }

        objectUrl = URL.createObjectURL(blob);
        if (!disposed) setPreview({ loading: false, type, source: objectUrl, error: "" });
      } catch (error) {
        if (!disposed) {
          setPreview({ loading: false, type, source: "", error: error.message || "文件预览失败" });
        }
      }
    }

    loadPreview();
    return () => {
      disposed = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [target]);

  function renderContent() {
    if (preview.error) {
      return <Alert type="error" showIcon message="文件预览失败" description={preview.error} />;
    }
    if (preview.type === "docx") {
      return (
        <div className="docx-preview-shell">
          <div ref={stylesRef} />
          <div ref={bodyRef} className="docx-preview-body" />
          {preview.loading && (
            <div className="preview-loading-overlay">
              <Spin size="large" tip="正在加载文件" />
            </div>
          )}
        </div>
      );
    }
    if (preview.loading) {
      return <div className="preview-loading"><Spin size="large" tip="正在加载文件" /></div>;
    }
    if (preview.type === "pdf") {
      return <iframe className="preview-pdf" title={target?.title} src={preview.source} />;
    }
    if (preview.type === "image") {
      return <div className="preview-image-wrap"><img src={preview.source} alt={target?.title} /></div>;
    }
    if (preview.type === "text") {
      return <pre className="preview-text">{preview.source}</pre>;
    }
    return <Empty description="暂不支持在线预览，请下载原文件查看" />;
  }

  return (
    <Modal
      className="document-preview-modal"
      title={<div><Text type="secondary">文件预览</Text><Title level={4}>{target?.title}</Title></div>}
      open={Boolean(target)}
      onCancel={onClose}
      footer={null}
      width={1080}
      destroyOnHidden
    >
      {renderContent()}
    </Modal>
  );
}

function BatchDeleteButton({ batchId, onDelete }) {
  return (
    <Popconfirm
      title="永久删除这个批次？"
      description="批次记录、所有作业文件和批改结果都会被真实删除，无法恢复。"
      okText="永久删除"
      cancelText="取消"
      okButtonProps={{ danger: true }}
      onConfirm={() => onDelete(batchId)}
    >
      <Button type="link" danger icon={<DeleteOutlined />}>删除</Button>
    </Popconfirm>
  );
}

function BatchDetail({ batch, onRefresh, onDelete, onPreview }) {
  const { message } = App.useApp();
  const errors = batch.summary?.common_errors || [];
  const [reviewing, setReviewing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [reviewForm] = Form.useForm();

  function openReview(item) {
    setReviewing(item);
    reviewForm.setFieldsValue({
      score: item.score,
      teacher_comment: item.result?.teacher_comment || "",
      deductions: item.result?.deductions || [],
    });
  }

  async function saveReview(values) {
    setSaving(true);
    try {
      await request(`/api/submissions/${reviewing.id}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      setReviewing(null);
      await onRefresh();
      message.success("批改结果已保存到 Word");
    } catch (error) {
      message.error(error.message);
    } finally {
      setSaving(false);
    }
  }

  const columns = [
    { title: "学生", dataIndex: "student_name" },
    { title: "文件", dataIndex: "filename", ellipsis: true },
    { title: "状态", dataIndex: "status", render: (status) => <Tag color={statusColor(status)}>{statusText[status] || status}</Tag> },
    { title: "错误", dataIndex: "error", ellipsis: true, render: (error) => error ? <Text type="danger" title={error}>{error}</Text> : "—" },
    { title: "得分", render: (_, item) => item.score == null ? "—" : `${item.score}${item.max_score ? ` / ${item.max_score}` : ""}` },
    {
      title: "操作",
      fixed: "right",
      render: (_, item) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => onPreview(item)}
          >
            查看
          </Button>
          <Button
            type="link"
            icon={<EditOutlined />}
            disabled={!["completed", "reviewed"].includes(item.status)}
            onClick={() => openReview(item)}
          >
            批改
          </Button>
          <Button
            type="link"
            icon={<DownloadOutlined />}
            disabled={!["completed", "reviewed"].includes(item.status)}
            href={`/api/submissions/${item.id}/file`}
          >
            下载
          </Button>
        </Space>
      ),
    },
  ];
  return (
    <Card className="detail-card" bordered={false}>
      <Flex justify="space-between" align="center" className="card-heading">
        <div>
          <Text className="eyebrow">批次详情</Text>
          <Title level={4}>{batch.name}</Title>
        </div>
        <Space>
          <Button
            icon={<DownloadOutlined />}
            disabled={!batch.submissions.some((item) => ["completed", "reviewed"].includes(item.status))}
            href={`/api/batches/${batch.id}/download`}
          >
            全部下载
          </Button>
          <BatchDeleteButton batchId={batch.id} onDelete={onDelete} />
          <Tag color={statusColor(batch.status)}>{statusText[batch.status] || batch.status}</Tag>
        </Space>
      </Flex>
      <Row gutter={14} className="detail-stats">
        <Col xs={24} sm={12} md={8}><Card size="small"><Statistic title="作业总数" value={batch.total} /></Card></Col>
        <Col xs={24} sm={12} md={8}><Card size="small"><Statistic title="已完成" value={batch.completed} /></Card></Col>
        <Col xs={24} sm={12} md={8}><Card size="small"><Statistic title="失败数量" value={batch.failed} /></Card></Col>
      </Row>
      {batch.status === "failed" && batch.summary?.error && <Alert type="error" showIcon message="批次处理失败" description={batch.summary.error} style={{ marginBottom: 18 }} />}
      <Progress percent={batch.total ? Math.round(((batch.completed + batch.failed) / batch.total) * 100) : 0} status={batch.failed ? "exception" : undefined} />
      <Title level={5}>共性问题</Title>
      {errors.length ? (
        <div className="common-errors">
          {errors.map((item) => {
            const example = item.examples?.[0];
            return (
              <div className="common-error" key={`${item.category}-${item.knowledge_point}`}>
                <Flex justify="space-between" align="start" gap={12}>
                  <Space wrap size={[8, 4]}>
                    <Tag color="orange">{item.category}</Tag>
                    <Text strong>{item.knowledge_point || item.category}</Text>
                  </Space>
                  <Text type="secondary" className="common-error-count">{item.count} 次</Text>
                </Flex>
                <Paragraph className="common-error-reason">
                  <Text type="secondary">错误表现：</Text>
                  {example?.reason || "未填写具体表现"}
                  {example?.question ? `（${example.question}）` : ""}
                </Paragraph>
                <Paragraph className="common-error-focus">
                  <Text strong>下节课重点：</Text>
                  {item.teaching_focus || "结合错误题目进行针对性讲解和练习"}
                </Paragraph>
                {example?.evidence && (
                  <Text type="secondary" className="common-error-evidence">
                    作业证据：{example.evidence}
                  </Text>
                )}
              </div>
            );
          })}
        </div>
      ) : <Text type="secondary">完成批改后，这里会出现高频错误。</Text>}
      <Table className="submission-table" rowKey="id" columns={columns} dataSource={batch.submissions} pagination={false} scroll={{ x: 880, y: 320 }} />
      <Modal
        title={<div><Text type="secondary">教师复核</Text><Title level={4} style={{ margin: "4px 0 0" }}>{reviewing?.student_name}</Title></div>}
        open={Boolean(reviewing)}
        onCancel={() => setReviewing(null)}
        footer={null}
        width={720}
        destroyOnHidden
      >
        <Form form={reviewForm} layout="vertical" onFinish={saveReview} className="review-form">
          <Flex gap={14}>
            <Form.Item label="最终得分" name="score" rules={[{ required: true, message: "请输入最终得分" }]} style={{ width: 180 }}>
              <InputNumber min={0} max={reviewing?.max_score} precision={2} style={{ width: "100%" }} />
            </Form.Item>
            <Form.Item label="原文件" style={{ flex: 1 }}>
              <Button icon={<DownloadOutlined />} href={reviewing ? `/api/submissions/${reviewing.id}/file` : undefined}>
                下载当前 Word
              </Button>
            </Form.Item>
          </Flex>
          <Divider orientation="left">扣分项</Divider>
          <Form.List name="deductions">
            {(fields, { add, remove }) => (
              <Flex vertical gap={10}>
                {fields.map(({ key, name, ...restField }) => (
                  <Card key={key} size="small" className="deduction-editor">
                    <Flex gap={8} align="start">
                      <Form.Item {...restField} name={[name, "question"]} label="题号/位置" style={{ width: 130 }}>
                        <Input placeholder="第 1 题" />
                      </Form.Item>
                      <Form.Item {...restField} name={[name, "points"]} label="扣分" style={{ width: 90 }}>
                        <InputNumber min={0} precision={2} style={{ width: "100%" }} />
                      </Form.Item>
                      <Form.Item {...restField} name={[name, "reason"]} label="扣分原因" style={{ flex: 1 }}>
                        <Input placeholder="说明错误和扣分依据" />
                      </Form.Item>
                      <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(name)} />
                    </Flex>
                    <Form.Item {...restField} name={[name, "evidence"]} label="原文证据" style={{ marginBottom: 0 }}>
                      <Input placeholder="可选，填写作业中的对应内容" />
                    </Form.Item>
                  </Card>
                ))}
                <Button onClick={() => add({ points: 0 })}>新增扣分项</Button>
              </Flex>
            )}
          </Form.List>
          <Form.Item label="教师评语" name="teacher_comment" style={{ marginTop: 18 }}>
            <Input.TextArea rows={3} placeholder="可选，补充给学生的反馈" />
          </Form.Item>
          <Flex justify="end" gap={10}>
            <Button onClick={() => setReviewing(null)}>取消</Button>
            <Button type="primary" htmlType="submit" loading={saving}>保存并写回 Word</Button>
          </Flex>
        </Form>
      </Modal>
    </Card>
  );
}

createRoot(document.getElementById("root")).render(
  <ConfigProvider
    theme={{
      token: {
        colorPrimary: "#2f6d5a",
        colorSuccess: "#4d8f69",
        colorInfo: "#2f6d5a",
        borderRadius: 10,
        fontFamily: '"Noto Sans SC", "Microsoft YaHei", sans-serif',
      },
      components: {
        Layout: { headerBg: "#203d35", siderBg: "#f7f9f5" },
        Card: { colorBgContainer: "#ffffff" },
      },
    }}
  >
    <App><AppShell /></App>
  </ConfigProvider>,
);
