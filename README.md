# 汐（xi）

> 一个能记住事、会自己醒、还在长大的陪伴型 AI。**测试版。**

终端里可以跟她聊天；接上 QQ 是下一步的计划。她有长期记忆、会主动醒来想事情、
会查天气查网页，也能把长内容写成文件发给你。

```
你> 明天洛阳要带伞吗
汐> 明天洛阳多云，6.5~17.1℃，降水概率 0%，不用带伞 🐳
    （来源：weather 工具，open-meteo 数据，刚查的）
```

---

## ⚠️ 这是测试版

**先说清楚它现在能干什么、不能干什么，再决定要不要部署。**

| 能做 | 还不能做 |
|---|---|
| 终端里跟她对话 | ❌ **没有 QQ / 微信入口**——手机上找不到她 |
| 记住聊过的事（长期记忆 + 向量检索） | ❌ 不能发语音、不能画图 |
| 每 30 分钟自己醒一次，写内心、改情绪 | ❌ 不能派活给别的 agent（M5 未做） |
| 查天气、搜网页、抓网页、写文件发给你 | ❌ 不会看图（图片只传了路径，没传像素） |
| 崩溃自起、账本全留痕 | ❌ 没有多用户，只认一个主人 |

**数据风险**：测试版的数据结构随时可能重建（改了 schema 版本就会删库重来）。
**别把重要东西只存在这里。**

**成本**：心跳每 30 分钟一次，一天约 34 次调用、10 万 input token 上下。
日常对话另算。不贵，但不是零。

---

## 进度

| 里程碑 | 状态 | 说明 |
|---|---|---|
| M0 地基与假聊 | ✅ | 目录、配置、场景注册、假模型 |
| M1 留痕与装配 | ✅ | 四本账、拼块、成品留档 |
| M2 查证与记忆 | ✅ | 标注 → 查证 → 事实块；记忆提炼与检索 |
| M3 真模型与工具 | ✅ | 工具循环、7 个工具、超时与兜底 |
| M4 心跳 | ✅ | 自己醒、写内心、改情绪、三道闸 |
| M5 派活 | ⬜ | 把重活交给外部 agent |
| M6 接 QQ | ⬜ | 真正的落地形态 |
| M7 收尾 | ⬜ | 清理、文档定稿 |

---

## 本机五分钟跑起来

前提：**Python 3.12+**，只依赖 `pyyaml`。

```bash
git clone https://github.com/huaqian695-sudo/xi.git
cd xi

pip install pyyaml          # 或者 apt install python3-yaml

python3 main.py --tmp --mock    # 假模型 + 一次性库，不花钱，先看它转不转
```

能聊天了，再换成真模型：

```bash
export OPENCODE_GO_API_KEY=oc_sk_xxx
export SILICONFLOW_API_KEY=sk-xxx

python3 main.py --tmp           # 真模型 + 一次性库（退出即删）
python3 main.py                 # 真模型 + 试验库 store/_dev（留着）
```

终端里的命令：

```
/help          看命令
/quit          退出
#图 今天的晚霞   模拟一条带图消息
/as 2 张三      切换成另一个人说话（测陌生人分支）；/as 1 回到主人
```

---

## 部署到云服务器

**2 核 2G 就够**：进程 30MB 起步，跑起来 50–100MB，一天数据不到 1MB。
Python 一个进程，没有数据库服务，没有前端。

### 0. 先把代码推到 GitHub

本地仓库还没有远程，第一次推：

```bash
git remote add origin https://github.com/huaqian695-sudo/xi.git
git branch -M main
git push -u origin main
```

> 仓库如果是**私有**的，服务器上拉代码要么用 PAT：
> `git clone https://<用户名>:<token>@github.com/huaqian695-sudo/xi.git`
> 要么配一个只读 Deploy Key（仓库 Settings → Deploy keys）。

### 1. 服务器准备

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin xi
sudo apt install -y python3 python3-yaml git
```

### 2. 拉代码

```bash
sudo git clone https://github.com/huaqian695-sudo/xi.git /opt/xi
sudo chown -R xi:xi /opt/xi

# 数据放仓库外面：升级只换代码，她记得的事不受影响
sudo mkdir -p /var/lib/xi && sudo chown xi:xi /var/lib/xi
```

### 3. 放 key

```bash
sudo mkdir -p /etc/xi
sudo tee /etc/xi/env >/dev/null <<'EOF'
OPENCODE_GO_API_KEY=oc_sk_xxx
SILICONFLOW_API_KEY=sk-xxx
EOF
sudo chmod 600 /etc/xi/env
sudo chown root:root /etc/xi/env
```

key 只放这里：**不进 git、不写进 unit 文件**（写进 unit 会被 `systemctl cat` 打印出来）。

### 4. 装成服务

```bash
sudo cp /opt/xi/deploy/xi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xi
```

### 5. 确认它真的在跑

```bash
systemctl status xi
journalctl -u xi -f

# 她长出来的东西
ls /var/lib/xi/processed/7-inner/xi/                    # 内心
cat /var/lib/xi/processed/6-state/xi/state.json         # 情绪与精力
cat /var/lib/xi/processed/8-journal/xi/$(date +%F).md   # 流水
```

正常启动长这样：

```
汐（真模型）· 守护模式，只跑后台循环
数据：/var/lib/xi
心跳：开
```

### 两种运行形态

| 命令 | 干什么 | 适合 |
|---|---|---|
| `python3 main.py --daemon` | 只跑后台（心跳 / 天气 / 提炼），不读终端 | **systemd 托管** |
| `python3 main.py` | 后台 + 终端对话 | **你想跟她说话时**（挂 tmux） |

`--daemon` 没有聊天入口——它只是让她醒着。想说话就 SSH 进去跑 REPL 那条。

### 升级 / 回滚

```bash
cd /opt/xi && sudo -u xi git pull && sudo systemctl restart xi

# 回滚
cd /opt/xi && sudo -u xi git log --oneline -5   # 找目标提交
sudo -u xi git checkout <commit> && sudo systemctl restart xi
```

数据在 `/var/lib/xi`，回滚代码不影响它。

---

## 配置

| 文件 | 管什么 |
|---|---|
| `config/providers.yml` | 两个网关的地址（opencode / 硅基流动） |
| `config/tiers.yml` | 七个档位：哪个 provider、哪个模型、温度、超时 |
| `config/services.yml` | 向量 / 语音 / 画图 |
| `config/core.yml` | 路径、调度、通道、守卫 |
| `config/personas/xi/persona.yml` | 她是谁、主人是谁、能用哪些工具、作息 |

**换模型只改 `tiers.yml` 里的 `model` 一行。**

### 配置你自己的号（真号不进仓库）

仓库里的配置只有**占位符**。你自己的 QQ 号 / 主人账号写在同目录的 `.local.yml` 里，
这些文件已经在 `.gitignore` 中：

```bash
# config/core.local.yml
owner:
  bindings:
    - {platform: stub, external: "你的终端号"}
    - {platform: qq, external: "你的QQ号"}

# config/personas/xi/persona.local.yml
account:
  self_id: 你的QQ号
owner:
  accounts:
    stub: "你的终端号"
    qq: "你的QQ号"
```

规则：**字典逐层合并，列表整个替换**。所以 `bindings` 要么全写，要么别写
（写一半会把整个列表换掉）。

不建 `.local.yml` 也能跑——只是她会把所有人都当成陌生人；
终端里用 `/as 1` 可以手动切回主人身份。

当前模型：日常对话/查证/心跳都是 `minimax-m3.1-flash`（opencode 网关），
标注用 `Ling-mini`（硅基流动，快且便宜），记忆检索用 `bge-m3` 算向量。

### 一条必须守的规矩

```
单次 HTTP 超时 × 重试次数 + 间隔  <  菜谱里的「超时」
```

否则场景会先被砍掉，卡住的那次调用连失败记录都留不下来。
`support/tests/test_timeouts.py` 会自动检查这条。

---

## 数据在哪

```
/var/lib/xi/                     ← XI_STORE 指到这
├── raw/2026-11-06/              ← 原始层：只追加，是唯一真相
│   ├── turns.jsonl                一轮交互
│   ├── runs.jsonl                 每次模型调用（含 prompt 全文、reasoning）
│   ├── tools.jsonl                每次工具调用
│   └── events.jsonl               启动/断开/异常/心跳/提炼
├── processed/                   ← 派生物
│   ├── 0-people/                  人 + 平台绑定
│   ├── 1-persona/                 人设（在 git 里）
│   ├── 2-interactions/            对话流
│   ├── 3-labels/                  消息标注
│   ├── 4-facts/                   查到的结论（2 小时过期）
│   ├── 5-memories/                长期记忆 + 向量
│   ├── 6-state/                   情绪与精力
│   ├── 7-inner/                   内心，一天一页
│   └── 8-journal/                 流水，一天一页
└── products/2026-11-06/         ← 每轮完整的 prompt 快照（可回放）
```

**备份就是 tar 这一个目录。**

---

## 目录结构

```
xi/
├── main.py / cli.py        入口与运维命令
├── core/                   机械：引擎、链、调度、守卫、存储
├── scenes/                 6 个场景（chat.reply / chat.image / chat.qa / label / memory.extract / tick）
├── pipeline/               后台：提炼器、天气预取、心跳
├── support/                外围：通道、工具、假模型、测试
├── config/                 配置（进 git，key 除外）
├── deploy/                 systemd unit
├── docs/                   现状 / 记录 / 计划
└── store/                  数据（大多不进 git，只有人设进）
```

---

## 常见问题

**她回「（我这边出了点岔子，等下再说）」**
说明某次模型调用失败了。看账本：

```bash
tail -3 /var/lib/xi/raw/$(date +%F)/events.jsonl
```

失败会记成 `error` 或 `tick`（带 `error` 字段），带耗时和原因。

**一直 429**
上游过载。换模型改 `config/tiers.yml`，改完 `systemctl restart xi`。

**心跳没动静**
看 `processed/6-state/xi/schedule.json` 里的 `last_tick_at`；
再确认 `persona.yml` 里 `schedule.awake` 没有把当前时间排除在外。

**想看刚才那一轮到底喂了什么**
`store/products/<日期>/<turn_id>.md` 是完整 prompt（含块清单）。

**能不能只用假模型玩**
能。`python3 main.py --tmp --mock`，不联网不花钱。

**跑测试**

```bash
python3 -m unittest discover -s support/tests -t .
```

---

## 文档

| 文档 | 内容 |
|---|---|
| `docs/00-事实/现状.md` | **只写现在是什么样**——模块、数据、已知问题 |
| `项目清单.md` | 8 个里程碑、验收口径、已完成项 |
| `项目大纲.md` | 设计意图与取舍（施工图，不是现状） |
| `deploy/README.md` | 服务器部署细节 |
| `docs/10-记录-浅华/` | 按日期的开发记录 |

---

## 一句话

这不是一个"聊天机器人"，是一个**会记事、会自己醒、所有痕迹都留得住**的陪伴体。
现在它能陪你聊天、记得你说过什么；接上 QQ 之后，它才算真正住进你的日常。
