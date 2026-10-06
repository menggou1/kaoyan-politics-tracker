# 考研政治刷题统计

一个基于 **Streamlit + SQLite** 的本地考研政治刷题统计工具，用来记录各章节的刷题进度、错题复刷情况和正确率，并自动生成学习报表。

数据保存在自己的电脑上，无需注册账号。原始操作以事件形式保存，统计结果可以随时重新计算。

## 功能

- **分章节统计**：马原、毛中特、新思想、史纲、思修分别维护进度与错题池，支持查看全局汇总。
- **一刷与二刷管理**：区分新题、错题复刷和顽固错题，支持按章节建立二刷基准。
- **正确率与错题追踪**：分别统计单选、多选题的作答量与正确率，核对错题余额。
- **灵活录入**：新题可按本次新增或当前累计录入，也可粘贴结构化文本填充表单。
- **学习报表**：支持日报、周报、月报与自选时间段报表，导出 Markdown、JSON 和 Session CSV。
- **备份与恢复**：支持自动备份、手动备份、撤销最近一次操作及从原始事件重算统计。

## 快速开始

### Windows 双击启动

先安装 **Python 3.11 或更高版本**，然后下载或克隆仓库：

```powershell
git clone https://github.com/menggou1/kaoyan-politics-tracker.git
cd kaoyan-politics-tracker
```

双击仓库根目录的 **`启动刷题统计.bat`**。启动器会打开浏览器；首次启动如缺少依赖，会自动建立虚拟环境并安装依赖，需要联网。

关闭服务时，双击 **`关闭刷题统计.bat`**。

### 手动启动

在仓库根目录打开 PowerShell，执行：

```powershell
cd politics_tracker
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

如果安装的是其他受支持的 Python 版本，请相应调整 `py -3.11`。

启动后访问 [http://localhost:8501](http://localhost:8501)。安装依赖后，日常使用无需联网。

## 使用流程

1. 在“新建记录”中选择章节与阶段。
2. 刷新题时填写新增题数或累计完成数，以及刷后的错题本余额；复刷错题时填写刷后余额与本轮再错数。
3. 检查本轮预览，确认后保存。
4. 在概览中查看进度，在“历史与报表”中查看或导出统计。
5. 某章节开始二刷前，在“导出与维护”中为该章节建立二刷基准。

阶段规则、结构化录入示例、历史数据导入与恢复说明见 [详细使用文档](politics_tracker/README.md)。

## 项目结构

```text
kaoyan-politics-tracker/
├── README.md                 # 仓库首页说明
├── 启动刷题统计.bat          # Windows 启动入口
├── 关闭刷题统计.bat          # Windows 关闭入口
└── politics_tracker/
    ├── app.py                # Streamlit 界面
    ├── src/                  # 计算、数据库、报表与备份逻辑
    ├── tests/                # 自动化测试
    ├── requirements.txt      # Python 依赖
    ├── README.md             # 详细使用文档
    ├── tracker.ps1           # Windows 服务启动与关闭脚本
    ├── seed_initial.py       # 可选的历史汇总导入脚本
    ├── data/                 # 本地 SQLite 数据库
    ├── reports/              # 生成的学习报表
    ├── attachments/          # 可选截图附件
    └── backups/              # 数据库备份
```

运行时目录按需生成。数据库、个人报表、截图、备份、日志和虚拟环境已配置为 Git 忽略项，不会随代码提交。

跨电脑迁移时，请另外复制 `politics_tracker/data/politics.db` 以及需要保留的截图附件。

## 开发与测试

在 `politics_tracker` 目录安装依赖后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖刷题计算、章节管理、结构化录入和期间报表。
