# Youtube Dashboard App

纯公开数据版 YouTube 多频道看板。

## 特点

- 不登录 YouTube 账号
- 不使用 OAuth
- 只读取 `YouTube Data API v3` 的公开数据
- 支持多个公开频道统一看板
- 支持频道别名，例如“员工1”
- 支持本地快照计算“较昨日播放增量”

## 运行要求

- Python 3
- Node.js / npx
- 一个有效的 `YouTube Data API v3` API Key

## 快速开始

1. 复制 `.env.example` 为 `.env`
2. 在 `.env` 里填写：

```env
YT_API_KEY=your_youtube_data_api_key
```

3. 运行：

```powershell
python setup_project.py --ensure-only
python youtube_dashboard_app.py --port 8130
```

4. 打开：

- [http://127.0.0.1:8130/](http://127.0.0.1:8130/)

或者直接双击：

- `一键配置环境.bat`
- `启动看板.bat`

## 当前看板内容

- 频道总播放量较昨日快照增量排行
- 频道当前订阅数排行
- 昨日发布视频当前总播放排行
- 今日发布视频汇总
- 昨日发布视频封面墙
- 频道公开画像总览
- 频道管理

## 文档

- [README_快速开始.md](./README_快速开始.md)
- [交付说明_快速交付版.md](./交付说明_快速交付版.md)
- [新电脑从零开始使用指南.md](./新电脑从零开始使用指南.md)

## 注意

不要把以下内容提交到公开仓库：

- `.env`
- `snapshots/`
- `output/`
- `tokens/`
- `youtube_tokens.json`
