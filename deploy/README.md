# 部署到云服务器

> 2026-11-06。目标机器：2 核 2G 就够（汐本体 30MB 起步，一天数据不到 1MB）。

## 现在部署能干什么、不能干什么

| | 状态 |
|---|---|
| 天气预取（每天 07:00）、提炼（每天 03:00）24 小时跑 | ✅ |
| 崩溃自起、日志进 journald | ✅ |
| **跟人聊天** | ❌ M6（QQ 通道）没做，只能 SSH 进终端聊 |

所以现在部署的实际价值是：**她一直在跑**。等 M6 接上 QQ，这台机器才真正有意义。

## 一、准备

```bash
# 1. 建用户（不给登录 shell，只用来跑服务）
sudo useradd --system --create-home --shell /usr/sbin/nologin xi

# 2. 代码放 /opt
sudo git clone <仓库地址> /opt/xi-project
sudo chown -R xi:xi /opt/xi-project

# 3. 数据放仓库外面（代码可以整块替换，数据不受影响）
sudo mkdir -p /var/lib/xi && sudo chown xi:xi /var/lib/xi

# 4. 依赖只有 pyyaml
sudo apt install -y python3 python3-yaml
# 或者用 venv：
# python3 -m venv /opt/xi-venv && /opt/xi-venv/bin/pip install pyyaml
```

## 二、key

```bash
sudo mkdir -p /etc/xi
sudo tee /etc/xi/env >/dev/null <<'EOF'
OPENCODE_GO_API_KEY=oc_sk_xxx
SILICONFLOW_API_KEY=sk-xxx
EOF
sudo chmod 600 /etc/xi/env
sudo chown root:root /etc/xi/env
```

**别把 key 写进仓库、也别写进 unit 文件**（unit 是 `${ }` 展开的，会进 `systemctl cat` 输出）。

## 三、装服务

```bash
sudo cp /opt/xi-project/deploy/xi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xi
```

## 四、确认它真的在跑

```bash
systemctl status xi            # active (running)
journalctl -u xi -f            # 实时日志

# 账本在长
ls /var/lib/xi/raw/$(date +%F)/

# 看事件流：启动、天气预取、提炼
tail -5 /var/lib/xi/raw/$(date +%F)/events.jsonl | python3 -m json.tool --json-lines
```

正常启动日志长这样：

```
汐（真模型）· 守护模式，只跑后台循环
数据：/var/lib/xi
收到 SIGTERM / Ctrl-C 就收尾退出
```

## 五、停 / 重启 / 升级

```bash
sudo systemctl stop xi      # 发 SIGTERM，程序写完 disconnect 再退（30 秒内）
sudo systemctl restart xi

# 升级
cd /opt/xi-project && sudo -u xi git pull && sudo systemctl restart xi
```

## 六、要知道的几件事

1. **数据在哪**：`/var/lib/xi`。备份就是 `tar` 这个目录；仓库整个删掉也不影响她。
2. **正式库守则不拦这里**：`guard_production_store()` 只拦仓库里的 `store/`。`XI_STORE=/var/lib/xi` 是部署路径，不受那条规矩限制。
3. **崩了会自己起**：`Restart=always`。连续崩会每 5 秒重试一次，`journalctl` 里能看出来。
4. **2G 内存够不够**：汐自身 30MB 起步，跑起来估 50–100MB。真正的内存大头是模型调用时的 prompt 和响应，都是短生命周期的。
5. **DSH 不装在这台机器上**：M5 派活按"投递任务 + 拉结果"设计，DSH 留在你自己的机器上跑。
