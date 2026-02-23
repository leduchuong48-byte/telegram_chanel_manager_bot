# Web Admin Panel 使用手册

本手册说明 Web 管理面板的安装、登录、配置编辑与热重载行为。

---

## 1. 安装依赖

安装 Web 面板所需依赖：

```bash
python3 -m pip install -r requirements.txt
```

关键依赖包括：
- fastapi
- uvicorn[standard]
- python-multipart（OAuth2 表单登录）
- python-jose[cryptography]
- passlib[bcrypt]

---

## 2. 快速启动

### 2.1 Docker 启动

```bash
export UID=$(id -u)
export GID=$(id -g)
docker compose up -d
```

### 2.2 本地启动

```bash
export TG_BOT_TOKEN="your_bot_token"
python3 web_app.py
```

服务默认监听 `0.0.0.0:8000`。登录入口：`http://localhost:8000/login`。

---

## 3. 登录与账号配置

配置文件位置：项目根目录 `config.json`。

在 `config.json` 中配置 `web_users`：

```json
{
  "web_users": [
    {
      "username": "admin",
      "password_hash": "$2b$12$..."
    }
  ]
}
```

### 3.1 生成 bcrypt 密码哈希

推荐使用工具脚本生成哈希：

```bash
python3 utils/password_gen.py "mypassword"
```

输出的字符串直接填入 `config.json` 的 `password_hash` 字段。

---

## 4. 功能说明

### 4.1 配置编辑器

- **保存 (Save)**：写入 `config.json`，并生成备份文件。
- **热重载 (Hot Reload)**：触发运行时配置刷新。

当前热重载仅会更新 `bot` 配置中的运行时字段并刷新日志级别：
- `dry_run`
- `delete_duplicates`
- `log_level`
- `tag_count`
- `tag_build_limit`

当前代码未包含调度器重启逻辑，因此如果涉及定时任务间隔或调度器配置变更，需要重启服务才能生效。

### 4.2 备份机制

每次保存配置后会生成备份文件：
- `./config.json.bak.<时间戳>`
- `./backups/config.json.bak.<时间戳>`

---

## 5. API 文档摘要

### 5.1 获取 Token

```bash
curl -X POST http://localhost:8000/api/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=admin&password=your_password"
```

### 5.2 获取配置

```bash
curl -X GET http://localhost:8000/api/config \
  -H "Authorization: Bearer <token>"
```

### 5.3 更新配置

```bash
curl -X PUT http://localhost:8000/api/config \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"data": {...}}'
```

---

## 6. 页面入口

- 登录页：`/login`
- 仪表盘：`/`
- 配置编辑器：`/config_editor`
