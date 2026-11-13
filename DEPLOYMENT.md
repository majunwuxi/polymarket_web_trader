# 部署指南

## 安装

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

cd /root/polymarketautotrader_dashboard
python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp config.sample.yaml config.yaml
cp .env.sample .env
chmod 600 .env config.yaml
```

编辑 `.env`，配置钱包、登录密码、2FA、Polygon RPC 和端口。

## 手动运行

```bash
source venv/bin/activate
python main.py
```

默认地址是 `http://localhost:5000`。

## systemd

```bash
sudo tee /etc/systemd/system/polymarket-dashboard.service > /dev/null <<'EOF'
[Unit]
Description=Polymarket Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/root/polymarketautotrader_dashboard
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/polymarketautotrader_dashboard/venv/bin/python /root/polymarketautotrader_dashboard/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now polymarket-dashboard
sudo systemctl status polymarket-dashboard
```

查看日志：

```bash
sudo journalctl -u polymarket-dashboard -f
```

## Nginx 反向代理

### 安装 Nginx

```bash
sudo apt update
sudo apt install -y nginx
sudo systemctl enable --now nginx
```

### 防火墙

```bash
sudo ufw allow 'Nginx Full'
sudo ufw status
```

### HTTP 配置（用于申请证书）

```bash
sudo tee /etc/nginx/sites-available/polymarket-dashboard <<'EOF'
server {
    listen 80;
    listen [::]:80;
    server_name spf.wellmax.uk;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/polymarket-dashboard /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

验证 HTTP 访问：

```bash
curl -I http://spf.wellmax.uk
```

### 配置 HTTPS（Let's Encrypt）

#### 安装 Certbot

```bash
sudo apt install -y certbot python3-certbot-nginx
```

#### 申请证书

```bash
sudo certbot --nginx -d spf.wellmax.uk --non-interactive --agree-tos --email your-email@example.com
```

> 将 `your-email@example.com` 替换为你的邮箱，用于接收证书过期通知。

Certbot 会自动修改 Nginx 配置，添加 SSL 相关指令并设置 HTTP→HTTPS 自动跳转。

#### 验证 HTTPS

```bash
curl -I https://spf.wellmax.uk
```

#### 自动续期

Certbot 安装后会自动添加 systemd timer 或 cron 任务。手动验证：

```bash
sudo certbot renew --dry-run
```

查看续期任务状态：

```bash
sudo systemctl list-timers | grep certbot
```

### 最终 HTTPS 配置参考

Certbot 自动生成的配置大致如下，可根据需要手动调整：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name spf.wellmax.uk;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name spf.wellmax.uk;

    ssl_certificate /etc/letsencrypt/live/spf.wellmax.uk/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/spf.wellmax.uk/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # 安全头
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
```

修改配置后重载 Nginx：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### 排错

| 问题 | 排查 |
|------|------|
| 访问返回 502 | 确认 Flask 服务在运行：`sudo systemctl status polymarket-dashboard` |
| 证书申请失败 | 确认 DNS 已解析到服务器 IP，80 端口对外开放：`sudo ufw status` |
| HTTPS 无法访问 | 确认 443 端口开放：`sudo ufw allow 443/tcp` |
| WebSocket 断开 | 确认 `proxy_set_header Upgrade` 和 `Connection "upgrade"` 已配置 |

## 常见问题

- 登录失败：检查 `DASHBOARD_PASSWORD` 和 `DASHBOARD_2FA_SECRET`。
- 持仓为空：检查钱包地址、Polymarket API 和 Polygon RPC。
- 下单失败：检查当前钱包、余额、授权、市场流动性和 CLOB API 返回错误。
