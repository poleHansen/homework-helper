# 作业批改工作台

一个运行在个人电脑上的本地作业批改工具。作业、评分标准和批改结果保存于本机，模型调用通过 LiteLLM 的 Python 接口完成。

## 启动

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

然后打开 `http://127.0.0.1:8000`。

## 配置

启动后可以在页面右上角打开“模型设置”，填写模型名称、API 地址、API 密钥和并发数，并点击“测试连接”确认 LiteLLM 可以正常调用。配置保存在本地 SQLite 中，修改后无需重启应用。

LiteLLM 通过环境变量读取模型配置：

```text
HOMEWORK_MODEL=openai/gpt-4o-mini
HOMEWORK_API_KEY=你的密钥
HOMEWORK_API_BASE=
GRADING_CONCURRENCY=3
```

项目会自动读取项目根目录下的 `.env` 文件。

页面配置优先于 `.env` 配置。模型未配置或调用失败时，批次会保存为失败状态，并在批次详情中显示具体错误。

## 当前闭环

- 本地维护多套评分标准；
- 每个批次绑定评分标准内容快照；
- 上传 ZIP 并提取 PDF、DOCX、TXT、Markdown、JPG、PNG；
- 使用异步并发调用 LiteLLM；
- 批次准备、批改和失败原因都有明确状态；
- 支持在页面测试模型连接；
- 保存每份作业的状态、分数、扣分项和批注；
- 统计批次中的高频错误；
- 原始文件和 SQLite 数据保存在 `data` 目录。

Jev 评判层暂留在批改服务的结构化结果边界内，后续可在不改变批次和结果数据结构的情况下接入。
