# BTC/ETH 市场情绪监控器（Streamlit）

一键部署的 Streamlit 应用，用于监控 BTC 与 ETH 的链上/机构信号并计算 Overheat Score（市场过热 / 超跌）。当触发阈值时，会发送邮件并通过 Server酱 推送微信通知。

## 文件
- `app.py` - 主应用（中文界面）
- `requirements.txt` - 依赖
- `README.md` - 本文件

## 快速部署（推荐：Streamlit Cloud）
1. 在 GitHub 新建仓库并将本项目文件上传（`app.py`, `requirements.txt`, `README.md`）。
   - 如果你不会命令行，可以直接在 GitHub 网页创建仓库，然后选择 **Add file → Upload files**，把上面三个文件拖入并提交（Commit）。

2. 登录 https://share.streamlit.io 并连接你的 GitHub，创建新 app，选择该仓库并指向 `app.py`。

3. 在 Streamlit App 的 Settings → Secrets 中添加以下密钥（**不要**把这些写入代码仓库）：

```
GMAIL_USER = "your_gmail@gmail.com"
GMAIL_APP_PASS = "<你的 Gmail App Password（请使用 App Password）>"
ALERT_EMAIL_TO = "your_alert_receiver@domain.com"
SERVERCHAN_SENDKEY = "SCTxxxxx"
GLASSNODE_API_KEY = "<optional: 如果有则填入>"
```

> Gmail 使用说明：  
> - 请对 Gmail 帐户启用两步验证（2FA），然后在 Google 帐户的安全设置中生成一个 App Password（类型选择 Mail），把生成的 16 位密码填入 `GMAIL_APP_PASS`（这是 Google 推荐的安全做法）。

4. 部署并打开 App，首次打开点击页面右侧「手动检测一次（立即）」以触发首次检测并生成历史缓存。之后应用会在后台每 3 小时自动运行（只要应用保持运行状态）。

## 本地运行（替代）
1. 克隆仓库并进入目录：
```bash
git clone <你的-repo-url>
cd <repo>
```
2. 创建虚拟环境并安装依赖：
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
3. 设置环境变量（示例 Bash）：
```bash
export GMAIL_USER="your_gmail@gmail.com"
export GMAIL_APP_PASS="<your_app_pass>"
export ALERT_EMAIL_TO="your_alert_receiver@domain.com"
export SERVERCHAN_SENDKEY="SCTxxxxx"
export GLASSNODE_API_KEY="<optional>"
```
4. 运行：
```bash
streamlit run app.py
```

## 测试与故障排查
- 手动检测：在 Streamlit 页面点击「手动检测一次（立即）」以确认邮件和微信是否能收到（测试时可临时把阈值调低以便触发）。
- 若邮件发送失败：确认 `GMAIL_APP_PASS` 是否为 App Password，`GMAIL_USER` 拼写是否正确，收件人地址是否为有效邮箱。
- 若 ServerChan 推送失败：确认 `SERVERCHAN_SENDKEY` 是否正确（可在 https://sct.ftqq.com/ 后台查看并重置）。
- 若链上指标为 0：说明未配置 `GLASSNODE_API_KEY` 或该指标不可用，建议注册 Glassnode 并把 Key 填入 Secrets。

## 隐私与安全
- 请不要把密钥写入仓库或公开渠道。使用 Streamlit Secrets 或 CI/CD 的 secrets 存储。
- 若怀疑 ServerChan SendKey 泄露，请在 ServerChan 控制台重置。
- 若需把通知改为企业微信/Slack/Telegram，可联系作者协助接入。

