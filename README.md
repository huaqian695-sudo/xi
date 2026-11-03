# 汐

一个住在 QQ 上的伙伴。蓝色大肥鱼，以 DeepSeek 为原型。

## 现在能干什么

M0 阶段：**假模型 + 假前台**。终端里敲一句话，屏幕回一句，不联网、不花钱。
她的回答是假的，但账是真的——每句话都记进 `store/raw/今天/`。

## 怎么跑

```bash
python3 main.py          # 进终端聊天（假模型）
python3 cli.py status    # 看服务状态
python3 cli.py doctor    # 自检

# 跑测试（标准库 unittest，不需要装东西）
python3 -m unittest discover -s support/tests -t .
```

## 东西在哪

| 位置 | 是什么 |
|---|---|
| `config/` | 五份配置：core / providers / tiers / services / personas |
| `store/raw/` | 原始层，一天一个文件夹，四本账 |
| `store/processed/` | 后处理层，11 个产品（人设、记忆、内心…） |
| `scenes/` | 一个场景一个文件夹（`scene.py` + `recipe.yml`） |
| `core/` | 人格无关的机械（引擎、防抖、外发…） |
| `support/` | 配套：通道、媒体、工具、测试 |
| `docs/` | 00-事实 / 10-记录-浅华 / 20-记录-汐 / 30-第三方报告 |

## 设计文档

- `项目大纲.md` —— 完整施工图（15 节 + 6 附录）
- `项目清单.md` —— 8 个里程碑 54 项

## 规矩

- 原始层只追加，永不修改、永不删除
- 后处理层全是派生物，可删可重建
- 改代码前先写日志，日志进 `docs/10-记录-浅华/`
