---
name: analyze-xhs-hot-posts
description: 采集并分析小红书关键词热门笔记，结合 BrowserAct 的原生搜索排序与 MediaCrawler 的详情、互动和评论数据，生成可解释的相对热度排行、标准化数据与中文创作洞察报告。用于用户要求“小红书热门帖分析”“关键词找爆款”“研究热门标题/正文/标签/评论需求”“输出小红书竞品数据集或排行榜”等非商业、小规模研究场景；不用于大规模采集、商业爬取、自动发布或绕过登录和访问控制。
---

# 小红书热门帖分析

## 开始前

把本 Skill 固定视为用户个人使用的非商业研究工具，无需每次询问或确认用途。只有用户明确要求商业使用或大规模采集时才停止，并说明 MediaCrawler 的许可证限制。不要承诺爆款，不要把样本内热度分描述为平台全站排名。

执行采集前读取：

- `references/workflow.md`：BrowserAct、MediaCrawler 的完整命令顺序和恢复策略。
- `references/policy.md`：许可证、隐私、速率和人工接管边界。
- 需要解释输出字段或评分时读取 `references/schema.md`。

## 默认值

- 时间范围：近半年。
- 笔记类型：全部。
- 采样：`balanced`，每个关键词三种原生排序各最多 40 条，融合后最多 80 个候选，补全前 30 篇，分析前 10 篇各最多 50 条一级评论。
- 输出目录：`/Users/xiyu/Desktop/self_media/xhs-hot-post-analysis/<run_id>/`。
- 封面分析：关闭；不下载图片、不做 OCR。

用户未提供关键词时必须询问，不要猜测。用户明确指定时间、类型、轻量采样或输出路径时覆盖默认值。

## 工作流

### 1. 预检并初始化

```bash
python3 scripts/preflight.py
python3 scripts/run.py init --keywords "关键词1,关键词2" --time-window 6m --note-type all --sampling-profile balanced
```

记录 `init` 输出的运行目录。后续所有命令都使用这个目录，不把运行数据写入 Skill 目录。

### 2. 用 BrowserAct 发现候选

先读取已安装的 `$browser-act` Skill，并完整运行：

```bash
browser-act get-skills core --skill-version 2.0.2
```

优先直接使用已配置的本地 Chrome 浏览器。打开会话、导航、读取页面、点击搜索筛选、滚动、执行提取脚本和关闭本 Skill 自己创建的会话均已获得用户授权，不要逐步询问。

只有 BrowserAct 自身规定必须确认的操作才暂停：新建/删除浏览器、导入浏览器资料、登录、提交表单、上传文件或代理变更。需要扫码、密码或人工验证码时调用 `remote-assist`，在用户回复前停止该会话的浏览器操作。

对每个关键词依次应用发布时间与笔记类型筛选，采集：

1. `popularity_descending`
2. `collect_descending`
3. `comment_descending`

不要并行请求同一浏览器。每次筛选后等待页面稳定、滚动到至少 40 条或 `has_more=false`，然后把提取结果管道写入运行目录：

```bash
python3 scripts/browseract/extract-search-feeds.py \
  | browser-act --session <session> eval --stdin \
  | python3 scripts/run.py record-feed --run-dir <run-dir> --keyword "<keyword>" --sort popularity_descending
```

三种排序记录完成后运行：

```bash
python3 scripts/run.py fuse --run-dir <run-dir>
```

### 3. 用 MediaCrawler 补全

```bash
python3 scripts/run.py crawl --run-dir <run-dir> --phase details
python3 scripts/run.py crawl --run-dir <run-dir> --phase comments
```

详情阶段不抓评论；评论阶段只处理每个关键词融合榜前 10 篇、每篇最多 50 条一级评论。固定并发为 1，不启用二级评论。若令牌失效，回到 BrowserAct 刷新对应候选一次；仍失败则记入失败列表并继续。

### 4. 生成分析包

```bash
python3 scripts/run.py finalize --run-dir <run-dir>
```

确认存在且非空：`report.md`、`ranked_posts.csv`、`posts.jsonl`、`comments.jsonl`、`run_manifest.json`。检查最终文件不含 `xsec_token`、原始用户 ID、IP、头像或评论者身份。

在确定性报告基础上补充创作洞察时：

- 每条判断附支持笔记链接和样本数。
- 分开标注“数据观察”与“编辑推断”。
- 只分析标题、正文、标签、形式、互动结构和评论需求。
- 不从互动量推断曝光率或因果关系，不把少量评论概括为全体用户。

## 失败处理

- BrowserAct 未安装或版本不兼容：按预检提示修复，不猜命令。
- 未登录、验证码或访问限制：暂停并人工接管；不得绕过。
- MediaCrawler 缺失：设置 `MEDIACRAWLER_DIR` 后重试。
- 空结果：生成带方法、失败原因和零样本说明的分析包，不伪造排行。
- 部分详情失败：保留候选预览字段，降低覆盖率并在报告与 manifest 中披露。
