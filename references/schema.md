# 数据与评分约定

## posts.jsonl

每行代表一个“关键词—笔记”样本：

- `keyword`, `note_id`, `note_url`, `nickname`, `note_type`
- `title`, `desc`, `published_at`, `age_days`, `tags`, `image_count`
- `likes`, `collects`, `comments`, `shares`
- `native_ranks`, `rrf_score`, `native_percentile`
- `engagement_score`, `velocity_score`, `hot_score`
- `collect_like_ratio`, `comment_like_ratio`, `share_like_ratio`
- `detail_status`

同一笔记命中多个关键词时允许出现多行，因为原生排名和样本内百分位随关键词变化。

## comments.jsonl

- `comment_key`：根据评论 ID 或内容生成的不可逆短哈希。
- `note_id`, `content`, `like_count`, `created_at`, `parent_comment_key`。

不保留评论者昵称、用户 ID、头像、IP 或图片。

## 热度分

先对每个关键词样本中的指标执行 `log1p`，再计算含并列处理的百分位。

```text
互动分 = 35% 点赞 + 30% 收藏 + 20% 评论 + 15% 分享
热度分 = 40% 原生融合排名 + 40% 互动分 + 20% 互动速度
```

互动速度使用 `count / max(age_days, 1)` 后再做 `log1p` 与百分位。缺少某个互动指标时只在已有指标间归一权重；缺少发布时间时使用 `50% 原生融合排名 + 50% 互动分`。

分数是当前关键词、当前采样窗口内的相对分，不可跨运行直接比较。

## 隐私字段

最终文件禁止出现：`xsec_token`、`user_id`、`author_id`、`creator_hash`、`ip_location`、`avatar`、`video_url`。公开昵称和去令牌笔记链接可以保留以便复核。

