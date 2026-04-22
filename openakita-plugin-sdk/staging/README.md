# openakita-plugin-sdk/staging/ — 0-消费者参考代码区

本目录是 SDK 0.7.0 整改后的代码归档区,存放**曾经在 SDK 公开 API 中、但没有任何首方插件实际消费**的模块。

## 性质

- **只是代码归档**。没有 `__init__.py`,**不是一个可导入的 Python 包**,不属于 `openakita-plugin-sdk` 公开 API。
- 不参与构建(`pyproject.toml` 不打包),不参与 CI(`pytest` 不发现,因为测试也已下架到这里)。
- 对应能力如有插件想用,**鼓励**:
  1. 把对应文件 `cp` 进自己插件目录,按需裁剪 → vendor 进插件;
  2. 或经评审升级为带 stable contract 的独立小包(如 `openakita-cost-tracker`),由 SDK 之外的项目维护。

## 子目录与顶层文件

```
staging/
├── README.md                    # 本文件
├── skill_loader.py              # SKILL.md frontmatter 解析器(曾在 SDK 顶层)
├── tests/
│   └── test_skill_loader.py     # 对应测试(已脱离 CI)
└── contrib/                     # 17 个 contrib.* 模块 + data/prompts/(参见子 README)
    ├── README.md
    ├── cost_estimator.py
    ├── cost_tracker.py
    ├── checkpoint.py
    ├── intent_verifier.py
    ├── ...
    └── data/prompts/
```

## 历史

- `contrib/`(17 模块) — 2026-04-19 ~ 2026-04-22 sprint 集中产出,详见 `contrib/README.md`。
- `skill_loader.py` — Sprint C0.4 产物,设想给"动态加载 SKILL.md"的插件场景用,但 host 端自有 `src/openakita/skills/loader.py` 完全独立,SDK 的这版从未被任何插件使用。

## 配套移动

- `web/`(bootstrap.js + ui-kit) → 不在 staging,改为 `plugins-archive/_shared/web-uikit/`(那才是真消费者:archive 里 19 个旧插件可能会按需 inline 它)。
- 见仓库根 `plugins-archive/_shared/web-uikit/README.md`。
