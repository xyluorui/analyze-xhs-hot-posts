# 执行工作流

## BrowserAct 筛选映射

| 参数 | 页面/API 值 |
|---|---|
| 最多点赞 | `popularity_descending` |
| 最多收藏 | `collect_descending` |
| 最多评论 | `comment_descending` |
| 全部类型 | `不限` |
| 视频 | `视频笔记` |
| 图文 | `普通笔记` |
| 近一天 | `一天内` |
| 近一周 | `一周内` |
| 近半年 | `半年内` |
| 不限时间 | `不限` |

筛选必须通过当前页面 `state` 获取元素索引，再点击对应语义的选项；页面变化后重新获取 `state`，不要复用旧索引。

每个排序至少收集 40 条或滚动至 `has_more=false`。页面详情访问之间等待 2–3 秒。候选记录中的 `xsec_token` 只保存在运行目录的 `.work` 临时文件中。

## 运行目录

```text
<run-dir>/
├── .work/                         # 临时令牌与 BrowserAct 原始记录
├── raw/mediacrawler/details/      # MediaCrawler 详情阶段
├── raw/mediacrawler/comments/     # MediaCrawler 评论阶段
├── posts.jsonl
├── comments.jsonl
├── ranked_posts.csv
├── report.md
└── run_manifest.json
```

`finalize` 成功后会删除 `.work` 中的令牌文件，只保留不含令牌的采样摘要。需要重新采集时重新运行 BrowserAct，不要复用旧令牌。

## 轻量采样

`light` 每个关键词只采集最多 40 个融合候选、补全前 20 篇、评论前 5 篇且每篇最多 20 条。仍使用三种原生排序，以免单一互动指标偏置结果。

## MediaCrawler

默认目录为 `/Users/xiyu/Documents/coding/github/MediaCrawler`，可用环境变量覆盖：

```bash
export MEDIACRAWLER_DIR=/absolute/path/to/MediaCrawler
```

`crawl` 使用参数数组调用 `uv run main.py`，不会拼接用户输入到 shell。运行采用 `xhs + detail + jsonl + concurrency=1`。登录、滑块或浏览器异常应由用户在可见浏览器中处理。

## 恢复

- `record-feed` 可重复调用；相同关键词和排序以最后一次成功记录为准。
- `fuse` 可安全重跑，会覆盖融合结果。
- `crawl` 写入阶段独立目录；重跑前使用新的运行目录，避免 JSONL 追加重复。
- `finalize` 对笔记、评论均去重，可安全重跑。

