# 中国榜热门网站首页扩充：选站人工审查

> 本文件由 `scripts/build_china_homepage_selection.py` 从冻结的 Semrush 中国 Top 100、现有 52 个首页案例、旧报告 10 个热门网站案例和独立 Chromium 根页观察机械生成。排名只用于选样，不参与得分。

## 计数核对

- 现有首页案例：52。
- 旧热门网站案例：10。
- 明确重复网站：amazon.com, reddit.com。
- 现有唯一网站域名：60。
- 严格补足到 100 个唯一网站需新增：40。

## 拟新增 40 个网站

| 中国榜名次 | 网站 | URL | 标签 | 首次加载结果 | 页面标题 |
|---:|---|---|---|---|---|
| 8 | `doubao.com` | https://doubao.com/ | `landing_page` | HTTP 200；199 字符 | 豆包 - 字节跳动旗下 AI 智能助手 |
| 12 | `feishu.cn` | https://feishu.cn/ | `landing_page` | HTTP 200；2776 字符 | 飞书｜字节跳动旗下AI工作平台 - 飞书官网 |
| 18 | `163.com` | https://163.com/ | `content_list` | HTTP 200；7629 字符 | 网易 |
| 19 | `taobao.com` | https://taobao.com/ | `product_catalog` | HTTP 200；2038 字符 | 淘宝 |
| 26 | `aliyun.com` | https://aliyun.com/ | `landing_page` | HTTP 200；9878 字符 | 阿里云-计算，为了无法计算的价值 |
| 28 | `weibo.com` | https://weibo.com/ | `content_list` | HTTP 200；1202 字符 | 微博 – 随时随地发现新鲜事 |
| 29 | `360.com` | https://360.com/ | `landing_page` | HTTP 200；2450 字符 | 360官网-360安全中心-360安全软件-360AI防护-360安全防护-360软件中心-360智能硬件-360企业服务 |
| 33 | `dingtalk.com` | https://dingtalk.com/ | `landing_page` | HTTP 200；3668 字符 | 钉钉，AI时代工作方式 |
| 36 | `hao123.com` | https://hao123.com/ | `content_list` | HTTP 200；4169 字符 | hao123_上网从这里开始 |
| 39 | `1688.com` | https://1688.com/ | `product_catalog` | HTTP 200；2249 字符 | 阿里1688首页 |
| 40 | `quark.cn` | https://quark.cn/ | `landing_page` | HTTP 200；4590 字符 | 夸克_AI旗舰应用官网 |
| 42 | `kdocs.cn` | https://kdocs.cn/ | `landing_page` | HTTP 200；1206 字符 | 金山文档 \| WPS云文档 |
| 44 | `kimi.com` | https://kimi.com/ | `landing_page` | HTTP 200；488 字符 | Kimi AI with K3 \| Built for Agentic Coding & Knowledge Work |
| 45 | `tmall.com` | https://tmall.com/ | `product_catalog` | HTTP 200；2092 字符 | 天猫Tmall.com - 买正品上天猫就购了 |
| 46 | `wedoany.com` | https://wedoany.com/ | `product_catalog` | HTTP 200；9504 字符 | 维度网 - 全球工业工程合作平台 - “一带一路”工业工程合作平台 |
| 47 | `qianwen.com` | https://qianwen.com/ | `landing_page` | HTTP 200；488 字符 | 千问-阿里 AI 助手 |
| 48 | `douyu.com` | https://douyu.com/ | `content_list` | HTTP 200；2147 字符 | 斗鱼 - 看见每种热爱 |
| 50 | `openai.com` | https://openai.com/ | `landing_page` | HTTP 200；4713 字符 | OpenAI \| Research & Deployment |
| 52 | `alipay.com` | https://alipay.com/ | `landing_page` | HTTP 200；166 字符 | 支付宝 |
| 53 | `sohu.com` | https://sohu.com/ | `content_list` | HTTP 200；5834 字符 | 搜狐 |
| 55 | `huya.com` | https://huya.com/ | `content_list` | HTTP 200；2438 字符 | 虎牙直播-技术驱动娱乐-弹幕式互动直播平台 |
| 56 | `cnki.net` | https://cnki.net/ | `content_list` | HTTP 200；3111 字符 | 中国知网 |
| 57 | `nih.gov` | https://nih.gov/ | `content_list` | HTTP 200；2339 字符 | National Institutes of Health (NIH) \| |
| 58 | `huaban.com` | https://huaban.com/ | `content_list` | HTTP 200；2522 字符 | 花瓣网 - 灵感之源，花瓣之间（创意图片大全、设计灵感图库、高清图片素材） |
| 59 | `jianying.com` | https://jianying.com/ | `landing_page` | HTTP 200；795 字符 | 剪映官网-剪映AI 创作无限新可能 |
| 60 | `eastmoney.com` | https://eastmoney.com/ | `content_list` | HTTP 200；13352 字符 | 东方财富网：财经门户，提供专业的财经、股票、行情、证券、基金、理财、银行、保险、信托、期货、黄金、股吧、博客等各类财经资讯及数据 |
| 63 | `sina.com.cn` | https://sina.com.cn/ | `content_list` | HTTP 200；9788 字符 | 新浪网 |
| 64 | `gamer520.com` | https://gamer520.com/ | `content_list` | HTTP 200；3136 字符 | Switch520-Gamer520 |
| 65 | `lingxing.com` | https://lingxing.com/ | `landing_page` | HTTP 200；2291 字符 | 跨境电商ERP软件平台_亚马逊ERP管理系统_跨境ERP-领星ERP官网 |
| 67 | `alibaba.com` | https://alibaba.com/ | `product_catalog` | HTTP 200；8708 字符 | Alibaba.com: Manufacturers, Suppliers, Exporters & Importers from the world's largest online B2B marketplace |
| 68 | `youdao.com` | https://youdao.com/ | `landing_page` | HTTP 200；717 字符 | 网易有道 |
| 69 | `3dmgame.com` | https://3dmgame.com/ | `content_list` | HTTP 200；8887 字符 | 单机游戏_单机游戏下载_单机游戏大全中文版下载_3DM游戏网 |
| 71 | `2345.com` | https://2345.com/ | `content_list` | HTTP 200；1902 字符 | 2345网址导航－致力于打造百年品牌（已创建21年01个月） |
| 72 | `cctv.com` | https://cctv.com/ | `content_list` | HTTP 200；6144 字符 | 央视网_世界就在眼前 |
| 73 | `so.com` | https://so.com/ | `landing_page` | HTTP 200；460 字符 | 360搜索，SO靠谱 |
| 75 | `volcengine.com` | https://volcengine.com/ | `landing_page` | HTTP 200；2349 字符 | 火山引擎-你的AI云 |
| 78 | `nga.cn` | https://nga.cn/ | `content_list` | HTTP 200；2244 字符 | NGA玩家社区 - NGA.CN |
| 80 | `yandex.com` | https://yandex.com/ | `landing_page` | HTTP 200；75 字符 | Yandex — fast Internet search |
| 81 | `elsevier.com` | https://elsevier.com/ | `landing_page` | HTTP 200；4239 字符 | Elsevier \| A global leader for advanced information and decision support in science and healthcare |
| 82 | `huawei.com` | https://huawei.com/ | `landing_page` | HTTP 200；2416 字符 | Huawei - Building a Fully Connected, Intelligent World |

## 标签口径

- `landing_page`：公开品牌、产品、服务或搜索入口页，按稳定页面身份与功能文字验收。
- `content_list`：首页直接呈现多条公开内容或栏目，规则可要求稳定文字和至少 3 条具名详情入口。
- `product_catalog`：首页直接呈现商品或供应目录，不冻结当前商品、价格、排名或顺序。
- 本轮没有为了凑标签而把首页替换成文章或正文详情页；`article`、`search_results`、`body_detail` 因实际入选根页形态不适用。

## 不合格候选

| 名次 | 网站 | 原因码 | 证据摘要 |
|---:|---|---|---|
| 4 | `msn.cn` | `consent_wall_only` | 首次无交互加载只呈现 Cookie/隐私同意层，缺少可用于任务验收的首页主体。 |
| 9 | `google.com.hk` | `duplicate_final_page` | 与现有 google.com 案例的自然落地首页相同。 |
| 11 | `facebook.com` | `login_page` | 首页主体是登录和注册表单。 |
| 13 | `instagram.com` | `login_page` | 首页主体是登录和注册表单。 |
| 14 | `x.com` | `access_denied` | HTTP 403 且可见正文为空。 |
| 20 | `woa.com` | `blank_or_error` | 最终进入 Chromium 错误页且正文为空。 |
| 23 | `linkedin.com` | `login_page` | 页面标题和主要操作均为登录或注册。 |
| 24 | `pinterest.com` | `login_page` | 匿名首页包含密码登录/注册界面；沿用现有全球榜的排除口径。 |
| 27 | `tencent.com` | `capture_error` | 独立 Chromium 未取得可审阅页面。 |
| 30 | `larkoffice.com` | `duplicate_final_page` | 自然重定向到 feishu.cn，与已选飞书首页完全重合。 |
| 34 | `tiktok.com` | `capture_error` | 独立 Chromium 未取得可审阅页面。 |
| 37 | `claude.ai` | `login_page` | 自然重定向到 /login。 |
| 38 | `alibaba-inc.com` | `capture_error` | 独立 Chromium 未取得可审阅页面。 |
| 43 | `live.com` | `blank` | 自然重定向到 Outlook，但渲染后正文长度为 0。 |
| 49 | `bytedance.net` | `capture_error` | 独立 Chromium 未取得可审阅页面。 |
| 61 | `msn.com` | `blank` | HTTP 200 但渲染后正文长度为 0。 |
| 62 | `sciencedirect.com` | `access_denied` | HTTP 403，页面只显示请求错误与支持编号。 |
| 74 | `temu.com` | `login_page` | 自然重定向到 /login.html。 |
| 77 | `antfin.com` | `blank_or_error` | 最终进入 Chromium 错误页且正文为空。 |
| 79 | `qcc.com` | `blank_or_error` | 最终进入 Chromium 错误页且正文为空。 |
| 84 | `zhipin.com` | `blank` | HTTP 200 但最终为 about:blank，正文为空。 |
| 86 | `aliyuncs.com` | `not_found` | 根页返回 HTTP 404。 |
| 93 | `360.cn` | `duplicate_final_page` | 与已选 360.com 自然落到同一套 360 官网内容；不作为独立网站案例重复计数。 |
| 98 | `office.com` | `duplicate_final_page` | 自然重定向到 m365.cloud.microsoft，与现有 cloud.microsoft 案例重合。 |

## 合格替补

下列页面可访问，但在名次更高的候选已补足 40 个后不进入本轮主集：

| 名次 | 网站 | 页面标题 |
|---:|---|---|
| 83 | `grok.com` | Grok |
| 85 | `shopify.com` | Shopify: The All-in-One Commerce Platform for Businesses - Shopify |
| 87 | `gamersky.com` | 游民星空 - 大型单机游戏门户 提供特色单机游戏资讯、下载 |
| 88 | `figma.com` | Figma: The collaborative canvas for design, code, and AI |
| 89 | `yuque.com` | 语雀，为每一个人提供优秀的文档和知识库工具 |
| 90 | `toutiao.com` | 今日头条 |
| 91 | `qingyue.vip` | XNP Social \| All-in-One TikTok Marketing & Analytics Platform |
| 92 | `linux.do` | (1) LINUX DO - 新的理想型社区 |
| 94 | `hupu.com` | 虎扑体育-虎扑网 |
| 95 | `iqiyi.com` | 爱奇艺 iQIYI-热门独播剧集在线观看 |
| 96 | `wps.cn` | WPS-支持多人在线协作编辑Word、Excel和PPT文档_WPS官方网站 |
| 97 | `cnblogs.com` | 博客园 - 开发者的网上家园 |

## 人工复核栏

- [ ] 同意将‘最终 100 个且不重复’解释为 60 个现有唯一域名 + 40 个新增域名。
- [ ] 同意以上 40 个网站及标签。
- [ ] 同意排除登录页、空白/错误页、重复最终页和只见同意层的页面。
- [ ] 同意规则只取首次直接加载证据，不点击、不递归，不冻结动态条目、价格和排行。
