# 中国榜热门网站首页扩充：规则人工审查

> 这是人工确认前的规则草案，由独立 Chromium 首次加载证据机械校验。没有调用 `lexmount-global`，没有生成供应商结果，也没有修改当前报告矩阵。

## 规则口径

| 页面标签 | 默认验收方式 | 本轮处理 |
|---|---|---|
| `landing_page` | 页面身份 | 全部稳定身份/主要功能文字组都要命中；不要求虚构列表。 |
| `content_list` | 优先具名链接 | URL 结构可靠时要求至少 3 条不同具名入口；身份藏在查询参数或链接不具名时，改用页面身份规则。 |
| `product_catalog` | 优先具名商品链接 | 有独立商品详情 URL 时要求至少 3 条；只有共享分类路径时，改用目录身份规则。 |
| `article` / `body_detail` | 正文覆盖 | 本轮严格保留榜单根页，没有此类入选页；不为凑标签改抓文章。 |
| `search_results` | 查询身份 + 结果结构 | 本轮没有以带查询词的结果页替换根页，因此没有此类入选页。 |

所有稳定文字组均要求命中，但组内候选词只需命中一个。动态条目、顺序、榜次、价格、数量和日期不固定，也不参与事实正确性判断。

## 汇总

- 规则总数：40。
- 标签：{"content_list": 16, "landing_page": 19, "product_catalog": 5}。
- 模型：{"named_link": 13, "page_identity": 27}。
- 机器可读规则：`research/china-homepage-rules.json`。

## 逐站规则

| 排名 | 网站 | 标签 | 模型 | 稳定文字组 | 证据中匹配链接数 |
|---:|---|---|---|---|---:|
| 8 | `doubao.com` | `landing_page` | `page_identity` | 豆包；有什么我能帮你的吗？；对话；图像生成 | 0 |
| 12 | `feishu.cn` | `landing_page` | `page_identity` | 飞书；字节跳动旗下 AI 工作平台；智能会议纪要；多维表格 | 0 |
| 18 | `163.com` | `content_list` | `named_link` | 网易；新闻；体育；财经 | 165 |
| 19 | `taobao.com` | `product_catalog` | `page_identity` | 淘宝；搜索；购物车；收藏夹 | 0 |
| 26 | `aliyun.com` | `landing_page` | `page_identity` | 阿里云；大模型；云市场；控制台；精选产品 | 0 |
| 28 | `weibo.com` | `content_list` | `page_identity` | 微博；热门推荐；热门榜单；热搜 | 0 |
| 29 | `360.com` | `landing_page` | `page_identity` | 360安全卫士；软件下载中心；纳米AI；360安全浏览器 | 0 |
| 33 | `dingtalk.com` | `landing_page` | `page_identity` | 钉钉；AI钉钉；产品功能；开放平台；下载钉钉 | 0 |
| 36 | `hao123.com` | `content_list` | `page_identity` | hao123；上网从这里开始；搜索热点；热点 | 0 |
| 39 | `1688.com` | `product_catalog` | `page_identity` | 阿里1688首页；挑好货；找工厂；工业品 | 0 |
| 40 | `quark.cn` | `landing_page` | `page_identity` | 夸克；AI浏览器；AI搜索；夸克网盘 | 0 |
| 42 | `kdocs.cn` | `landing_page` | `page_identity` | 金山文档；WPS云文档；智能文档；多人协作 | 0 |
| 44 | `kimi.com` | `landing_page` | `page_identity` | Kimi；New Chat；Deep Research；Kimi Code | 0 |
| 45 | `tmall.com` | `product_catalog` | `page_identity` | 天猫Tmall.com；搜索；购物车；商家支持 | 0 |
| 46 | `wedoany.com` | `product_catalog` | `named_link` | 维度网；工程分类；产品；选型总库 | 102 |
| 47 | `qianwen.com` | `landing_page` | `page_identity` | 千问；阿里旗下全能AI助手；新建对话；千问创作 | 0 |
| 48 | `douyu.com` | `content_list` | `named_link` | 斗鱼；直播；分类；视频 | 50 |
| 50 | `openai.com` | `landing_page` | `page_identity` | OpenAI；Research；Products；Developers；Try ChatGPT | 0 |
| 52 | `alipay.com` | `landing_page` | `page_identity` | 支付宝；我是服务商；我是开发者；我是商家用户 | 0 |
| 53 | `sohu.com` | `content_list` | `named_link` | 搜狐；新闻；财经；体育 | 167 |
| 55 | `huya.com` | `content_list` | `named_link` | 虎牙直播；绝地求生；英雄联盟；王者荣耀 | 29 |
| 56 | `cnki.net` | `content_list` | `page_identity` | 中国知网；学术期刊；学位论文；下载热榜 | 0 |
| 57 | `nih.gov` | `content_list` | `page_identity` | National Institutes of Health；Health Information；Grants & Funding；NIH at a Glance | 0 |
| 58 | `huaban.com` | `content_list` | `named_link` | 花瓣网；探索；平面设计；UI/UX | 32 |
| 59 | `jianying.com` | `landing_page` | `page_identity` | 剪映AI；视频生成；专业剪辑；智能抠像 | 0 |
| 60 | `eastmoney.com` | `content_list` | `named_link` | 东方财富网；财经；股票；基金 | 166 |
| 63 | `sina.com.cn` | `content_list` | `named_link` | 新浪网；新闻；财经；体育 | 179 |
| 64 | `gamer520.com` | `content_list` | `named_link` | Switch520；PC游戏；IGN必玩推荐榜 | 45 |
| 65 | `lingxing.com` | `landing_page` | `page_identity` | 领星ERP；核心功能；进销存一体管理；财务精准管理 | 0 |
| 67 | `alibaba.com` | `product_catalog` | `named_link` | Alibaba.com；Discover manufacturers；Request for Quotation；Top Ranking | 36 |
| 68 | `youdao.com` | `landing_page` | `page_identity` | 网易有道；词典；翻译；有道龙虾 | 0 |
| 69 | `3dmgame.com` | `content_list` | `named_link` | 单机游戏；新闻；专栏；硬件 | 241 |
| 71 | `2345.com` | `content_list` | `named_link` | 2345网址导航；旗下产品；邮箱；天气 | 24 |
| 72 | `cctv.com` | `content_list` | `named_link` | 央视网；新闻；财经；体育 | 96 |
| 73 | `so.com` | `landing_page` | `page_identity` | 360搜索；搜全网；问AI；AI写作 | 0 |
| 75 | `volcengine.com` | `landing_page` | `page_identity` | 火山引擎；火山方舟；精选产品；控制台 | 0 |
| 78 | `nga.cn` | `content_list` | `page_identity` | NGA玩家社区；论坛；评分；专题 | 0 |
| 80 | `yandex.com` | `landing_page` | `page_identity` | Yandex；Search with Yandex AI；Maps | 0 |
| 81 | `elsevier.com` | `landing_page` | `page_identity` | Elsevier；Academic & Government；Health；Publish with us | 0 |
| 82 | `huawei.com` | `landing_page` | `page_identity` | Huawei；Enterprise；Consumer；Huawei Cloud | 0 |

## 逐站详情

### 8. `doubao.com`

- 请求 URL：https://doubao.com/
- 首次加载最终 URL：https://www.doubao.com/chat/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：豆包；有什么我能帮你的吗？；对话；图像生成
- [ ] 规则同意

### 12. `feishu.cn`

- 请求 URL：https://feishu.cn/
- 首次加载最终 URL：https://www.feishu.cn/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：飞书；字节跳动旗下 AI 工作平台；智能会议纪要；多维表格
- [ ] 规则同意

### 18. `163.com`

- 请求 URL：https://163.com/
- 首次加载最终 URL：https://www.163.com/?referFrom=
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：网易；新闻；体育；财经
- 链接契约：主机 `www.163.com`；路径正则 `/dy/article/[A-Z0-9]+\.html`；至少 3 条。
- 独立证据匹配：165 条。以下仅是观察样例，不冻结标题或成员：
  - 扩大双向投资共促全球发展 — https://www.163.com/dy/article/L6HB2KV90514R9M0.html?clickfrom=w_yw
  - 倒下，为了蹚出通向胜利的道路 — https://www.163.com/dy/article/L6GCDIOQ05346RC6.html?clickfrom=w_yw
  - 在AI微短剧里，他们找到了就业新赛道 — https://www.163.com/dy/article/L6HC6LQE0530QRMB.html?clickfrom=w_yw
- [ ] 规则同意

### 19. `taobao.com`

- 请求 URL：https://taobao.com/
- 首次加载最终 URL：https://www.taobao.com/
- 标签 / 模型：`product_catalog` / `page_identity`
- 规则理由：商品 ID 位于 item.htm 的查询参数中，共享提取器按主机与路径去重；暂按目录身份核验。
- 稳定文字：淘宝；搜索；购物车；收藏夹
- [ ] 规则同意

### 26. `aliyun.com`

- 请求 URL：https://aliyun.com/
- 首次加载最终 URL：https://www.aliyun.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：阿里云；大模型；云市场；控制台；精选产品
- [ ] 规则同意

### 28. `weibo.com`

- 请求 URL：https://weibo.com/
- 首次加载最终 URL：https://weibo.com/newlogin?tabtype=weibo&gid=102803&openLoginLayer=0&url=https://weibo.com/
- 标签 / 模型：`content_list` / `page_identity`
- 规则理由：观察到公开内容流，但文章链接的可见标签主要是时间而非标题，故不把它们伪装成具名记录。
- 稳定文字：微博；热门推荐；热门榜单；热搜
- [ ] 规则同意

### 29. `360.com`

- 请求 URL：https://360.com/
- 首次加载最终 URL：https://360.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：360安全卫士；软件下载中心；纳米AI；360安全浏览器
- [ ] 规则同意

### 33. `dingtalk.com`

- 请求 URL：https://dingtalk.com/
- 首次加载最终 URL：https://www.dingtalk.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：钉钉；AI钉钉；产品功能；开放平台；下载钉钉
- [ ] 规则同意

### 36. `hao123.com`

- 请求 URL：https://hao123.com/
- 首次加载最终 URL：https://www.hao123.com/?from=offline_host
- 标签 / 模型：`content_list` / `page_identity`
- 规则理由：新闻链接以查询参数承载身份，当前共享提取器会按主机与路径去重；暂按页面身份核验。
- 稳定文字：hao123；上网从这里开始；搜索热点；热点
- [ ] 规则同意

### 39. `1688.com`

- 请求 URL：https://1688.com/
- 首次加载最终 URL：https://www.1688.com/
- 标签 / 模型：`product_catalog` / `page_identity`
- 规则理由：商品分类链接共用 /selloffer/offer_search.htm，身份在查询参数中；暂按目录身份核验。
- 稳定文字：阿里1688首页；挑好货；找工厂；工业品
- [ ] 规则同意

### 40. `quark.cn`

- 请求 URL：https://quark.cn/
- 首次加载最终 URL：https://www.quark.cn/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：夸克；AI浏览器；AI搜索；夸克网盘
- [ ] 规则同意

### 42. `kdocs.cn`

- 请求 URL：https://kdocs.cn/
- 首次加载最终 URL：https://www.kdocs.cn/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：金山文档；WPS云文档；智能文档；多人协作
- [ ] 规则同意

### 44. `kimi.com`

- 请求 URL：https://kimi.com/
- 首次加载最终 URL：https://www.kimi.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：Kimi；New Chat；Deep Research；Kimi Code
- [ ] 规则同意

### 45. `tmall.com`

- 请求 URL：https://tmall.com/
- 首次加载最终 URL：https://www.tmall.com/
- 标签 / 模型：`product_catalog` / `page_identity`
- 规则理由：商品 ID 位于 item.htm 的查询参数中，共享提取器按主机与路径去重；暂按目录身份核验。
- 稳定文字：天猫Tmall.com；搜索；购物车；商家支持
- [ ] 规则同意

### 46. `wedoany.com`

- 请求 URL：https://wedoany.com/
- 首次加载最终 URL：https://www.wedoany.com/
- 标签 / 模型：`product_catalog` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：维度网；工程分类；产品；选型总库
- 链接契约：主机 `www.wedoany.com`；路径正则 `/product/[0-9]+\.html`；至少 3 条。
- 独立证据匹配：102 条。以下仅是观察样例，不冻结标题或成员：
  - 沟通报价 高速动车组平台 中车长春轨道客车股份有限公司 — https://www.wedoany.com/product/190151.html
  - 沟通报价 车用8Y系列发动机 上海新动力汽车科技股份有限公司 — https://www.wedoany.com/product/183696.html
  - ¥350-450 /千克 中航迈特Avimetal增材制造3d打印TC4钛合金粉末超细球型Ti6AI4V金属粉末 中航迈特增材科技（北京）有限公司 — https://www.wedoany.com/product/183041.html
- [ ] 规则同意

### 47. `qianwen.com`

- 请求 URL：https://qianwen.com/
- 首次加载最终 URL：https://www.qianwen.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：千问；阿里旗下全能AI助手；新建对话；千问创作
- [ ] 规则同意

### 48. `douyu.com`

- 请求 URL：https://douyu.com/
- 首次加载最终 URL：https://www.douyu.com/?dyshid=0-
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：斗鱼；直播；分类；视频
- 链接契约：主机 `www.douyu.com`；路径正则 `/[0-9]+`；至少 3 条。
- 独立证据匹配：50 条。以下仅是观察样例，不冻结标题或成员：
  - 斗鱼吃鸡赛事 — https://www.douyu.com/100
  - 火影忍者趣味上超挑战赛 — https://www.douyu.com/7839031
  - 完美定级赛！ 11969011 — https://www.douyu.com/11969011
- [ ] 规则同意

### 50. `openai.com`

- 请求 URL：https://openai.com/
- 首次加载最终 URL：https://openai.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：OpenAI；Research；Products；Developers；Try ChatGPT
- [ ] 规则同意

### 52. `alipay.com`

- 请求 URL：https://alipay.com/
- 首次加载最终 URL：https://www.alipay.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：支付宝；我是服务商；我是开发者；我是商家用户
- [ ] 规则同意

### 53. `sohu.com`

- 请求 URL：https://sohu.com/
- 首次加载最终 URL：https://www.sohu.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：搜狐；新闻；财经；体育
- 链接契约：主机 `www.sohu.com`；路径正则 `/a/[0-9]+_[0-9]+`；至少 3 条。
- 独立证据匹配：167 条。以下仅是观察样例，不冻结标题或成员：
  - 中秋假期首日火车票开售 购票日历请收好 — https://www.sohu.com/a/1074816154_120914498?edtsign=53EC0CFADABCB793650352966C0A8257F356AA0E&edtcode=t2ghGd6SBUJwCxJjFvW8hQ%3D%3D&scm=thor.283_14-200000.0.0-0-0-0-0.&spm=smpc.home.fspic.1.1789122990639O2VR1Qs_1467
  - 50天内台风接连登陆地设"三台风纪念碑" — https://www.sohu.com/a/1074812845_120388781?edtsign=9BB9B8028C84677F55351F927E622762B822E716&edtcode=t2ghGd6SBUJwCxJjFvW8hQ%3D%3D&scm=thor.283_14-200000.0.0-0-0-0-0.&spm=smpc.home.fspic.2.1789122990639O2VR1Qs_1467
  - 雀巢持续"瘦身" 接连卖掉7大品牌业务 — https://www.sohu.com/a/1074842666_313745?edtsign=1493283E56F9AB15DBA2070E1359F7EF7A43B4D9&edtcode=t2ghGd6SBUJwCxJjFvW8hQ%3D%3D&scm=thor.283_14-200000.0.0-0-0-0-0.&spm=smpc.home.fspic.4.1789122990639O2VR1Qs_1467
- [ ] 规则同意

### 55. `huya.com`

- 请求 URL：https://huya.com/
- 首次加载最终 URL：https://www.huya.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：虎牙直播；绝地求生；英雄联盟；王者荣耀
- 链接契约：主机 `www.huya.com`；路径正则 `/video/u/[0-9]+`；至少 3 条。
- 独立证据匹配：29 条。以下仅是观察样例，不冻结标题或成员：
  - RASH悲喜 — http://www.huya.com/video/u/1487476672
  - 沫子 — http://www.huya.com/video/u/3197597
  - 托马斯Czh — http://www.huya.com/video/u/1199512718563
- [ ] 规则同意

### 56. `cnki.net`

- 请求 URL：https://cnki.net/
- 首次加载最终 URL：https://www.cnki.net/
- 标签 / 模型：`content_list` / `page_identity`
- 规则理由：文献身份位于查询参数，而共享提取器按主机与路径去重；规则不冻结当前论文标题。
- 稳定文字：中国知网；学术期刊；学位论文；下载热榜
- [ ] 规则同意

### 57. `nih.gov`

- 请求 URL：https://nih.gov/
- 首次加载最终 URL：https://www.nih.gov/
- 标签 / 模型：`content_list` / `page_identity`
- 规则理由：首页混合专题、跨 NIH 机构资源与栏目入口；没有单一、稳定的详情 URL 形状。
- 稳定文字：National Institutes of Health；Health Information；Grants & Funding；NIH at a Glance
- [ ] 规则同意

### 58. `huaban.com`

- 请求 URL：https://huaban.com/
- 首次加载最终 URL：https://huaban.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：花瓣网；探索；平面设计；UI/UX
- 链接契约：主机 `huaban.com`；路径正则 `/pins/[0-9]+`；至少 3 条。
- 独立证据匹配：32 条。以下仅是观察样例，不冻结标题或成员：
  - 中秋节海报 — https://huaban.com/pins/6771386994
  - 已生成图片：本草之源，滋养身心 — https://huaban.com/pins/7117195745
  - 携手热爱奔赴山河励志手写字体字体设计 — https://huaban.com/pins/6225051108
- [ ] 规则同意

### 59. `jianying.com`

- 请求 URL：https://jianying.com/
- 首次加载最终 URL：https://www.jianying.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：剪映AI；视频生成；专业剪辑；智能抠像
- [ ] 规则同意

### 60. `eastmoney.com`

- 请求 URL：https://eastmoney.com/
- 首次加载最终 URL：https://www.eastmoney.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：东方财富网；财经；股票；基金
- 链接契约：主机 `finance.eastmoney.com`；路径正则 `/a/[0-9]+\.html`；至少 3 条。
- 独立证据匹配：166 条。以下仅是观察样例，不冻结标题或成员：
  - 美联储下周是否加息？今晚20:30 市场迎来“终极一战”！ — https://finance.eastmoney.com/a/202609113872026872.html
  - 美联储下周加息概率升破70% 华尔街紧盯今晚CPI — https://finance.eastmoney.com/a/202609113871241897.html
  - A股三大指数收跌 地面兵装、玻璃玻纤、元件板块逆市走强 — https://finance.eastmoney.com/a/202609113872026127.html
- [ ] 规则同意

### 63. `sina.com.cn`

- 请求 URL：https://sina.com.cn/
- 首次加载最终 URL：https://www.sina.com.cn/?from=kandian
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：新浪网；新闻；财经；体育
- 链接契约：主机 `news.sina.com.cn, finance.sina.com.cn, sports.sina.com.cn`；路径正则 `/.*/[0-9]{4}-[0-9]{2}-[0-9]{2}/[^/]+\.shtml`；至少 3 条。
- 独立证据匹配：179 条。以下仅是观察样例，不冻结标题或成员：
  - 牛弹琴：这个国家，快把兄弟们得罪光了 — https://finance.sina.com.cn/world/2026-09-11/doc-inirmfef5320805.shtml
  - 看清经济分化与集聚 — http://finance.sina.com.cn/zl/china/2026-09-10/zl-inirinus8532514.shtml
  - 员工如厕被拒失控“裸奔”？ — https://finance.sina.com.cn/wm/2026-09-10/doc-inirhezn3433533.shtml
- [ ] 规则同意

### 64. `gamer520.com`

- 请求 URL：https://gamer520.com/
- 首次加载最终 URL：https://www.gamer520.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：Switch520；PC游戏；IGN必玩推荐榜
- 链接契约：主机 `www.gamer520.com`；路径正则 `/[0-9]+\.html`；至少 3 条。
- 独立证据匹配：45 条。以下仅是观察样例，不冻结标题或成员：
  - 歧路旅人0|豪华中文|V1.0.3-故土新塑-无冕御灵+全DLC-模拟器整合版|解压即撸| — https://www.gamer520.com/103203.html
  - 情感反诈模拟器|中字-国语|Build.19395921-心战术师-破镜重生+全DLC-多结局|解压即撸| — https://www.gamer520.com/94895.html
  - 真・三国无双 起源|中字-国语|V1.0.1.2Fix+梦幻四英杰DLC-实装马匹“金兔马”+预购特典+全DLC+修改器-支持手柄|解压即撸| — https://www.gamer520.com/85688.html
- [ ] 规则同意

### 65. `lingxing.com`

- 请求 URL：https://lingxing.com/
- 首次加载最终 URL：https://www.lingxing.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：领星ERP；核心功能；进销存一体管理；财务精准管理
- [ ] 规则同意

### 67. `alibaba.com`

- 请求 URL：https://alibaba.com/
- 首次加载最终 URL：https://www.alibaba.com/
- 标签 / 模型：`product_catalog` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：Alibaba.com；Discover manufacturers；Request for Quotation；Top Ranking
- 链接契约：主机 `www.alibaba.com`；路径正则 `/product-detail/.+`；至少 3 条。
- 独立证据匹配：36 条。以下仅是观察样例，不冻结标题或成员：
  - 15kWh Wall-Mount LiFePO4 Battery Pack, 48V 300Ah, Built-in BMS, >6000 Cycles, for Home Solar Energy Storage, UN38.3/CE/IEC 5% off CN¥13,600 Reorder rate 18% CN¥2,189.60-2,723.65 MOQ: 1 piece184 sold 1 yr CN — https://www.alibaba.com/product-detail/15kWh-Wall-Mount-LiFePO4-Battery-Pack_1601610545341.html?priceId=c308d0f5623640aa85fdc9cf30e3f6dd
  - WLX Factory 8-level Gradient Strength Foot Massager Machine 240mah 6 Kinds Ems Foot Massager Mat Cushion CN¥5.68-6.68 MOQ: 1 piece8,401 sold 2 yrs CN — https://www.alibaba.com/product-detail/WLX-Factory-8-level-Gradient-Strength_1601475103198.html?priceId=c308d0f5623640aa85fdc9cf30e3f6dd
  - Piano Black Brown Highlight 300% Density Machine Made Layered Curly Lace Front Wig Vietnamese Raw Human Hair Glueless 5% off CN¥13,600 Reorder rate 27% CN¥66.09-266.36 MOQ: 1 piece695 sold 6 yrs CN — https://www.alibaba.com/product-detail/Piano-Black-Brown-Highlight-300-Density_1601735401654.html?priceId=c308d0f5623640aa85fdc9cf30e3f6dd
- [ ] 规则同意

### 68. `youdao.com`

- 请求 URL：https://youdao.com/
- 首次加载最终 URL：https://youdao.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：网易有道；词典；翻译；有道龙虾
- [ ] 规则同意

### 69. `3dmgame.com`

- 请求 URL：https://3dmgame.com/
- 首次加载最终 URL：https://www.3dmgame.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：单机游戏；新闻；专栏；硬件
- 链接契约：主机 `www.3dmgame.com`；路径正则 `/games/[A-Za-z0-9_-]+`；至少 3 条。
- 独立证据匹配：241 条。以下仅是观察样例，不冻结标题或成员：
  - 鬼武者：剑之道 — https://www.3dmgame.com/games/gwzjzd/
  - 2 生化危机9：安魂曲 — https://www.3dmgame.com/games/shwj9/
  - 红色沙漠 — https://www.3dmgame.com/games/hssm/
- [ ] 规则同意

### 71. `2345.com`

- 请求 URL：https://2345.com/
- 首次加载最终 URL：https://www.2345.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：具名内容来自首页直接公开的资讯卡片；第三方承载主机可能变化，冻结前应再复查。
- 稳定文字：2345网址导航；旗下产品；邮箱；天气
- 链接契约：主机 `news.jxcn.cn, news-pc.gzylhy.cn`；路径正则 `/p/[0-9]{8}/[0-9]+\.html`；至少 3 条。
- 独立证据匹配：24 条。以下仅是观察样例，不冻结标题或成员：
  - 洪水之下，60岁母亲被毒蛇咬伤身亡背后 — https://news.jxcn.cn/p/20260911/43037089.html?from=jx39
  - 特朗普称美伊战事将在中期选举后“立即终结” — https://news.jxcn.cn/p/20260910/43036562.html?from=jx52
  - 国际粮价飙涨，会不会影响我们的“饭碗”？ — https://news.jxcn.cn/p/20260909/43035967.html?from=jx80
- [ ] 规则同意

### 72. `cctv.com`

- 请求 URL：https://cctv.com/
- 首次加载最终 URL：https://www.cctv.com/
- 标签 / 模型：`content_list` / `named_link`
- 规则理由：首页存在至少 3 个符合单一稳定 URL 结构的具名条目。
- 稳定文字：央视网；新闻；财经；体育
- 链接契约：主机 `news.cctv.com, tv.cctv.com`；路径正则 `/[0-9]{4}/[0-9]{2}/[0-9]{2}/(?:ARTI|VIDE)[A-Za-z0-9]+\.shtml`；至少 3 条。
- 独立证据匹配：96 条。以下仅是观察样例，不冻结标题或成员：
  - 联播+｜“全球南方”第一方阵 — https://news.cctv.com/2026/09/11/ARTITnBDychMF9rMGWFyjGT2260909.shtml
  - 截至2025年末 中国对金砖国家直接投资存量820亿美元 ｜ — https://tv.cctv.com/2026/09/11/VIDE4LI6THQoWl3WkKmfPQ0z260911.shtml
  - 金砖国家已达成60余份成果文件 — https://tv.cctv.com/2026/09/11/VIDEykrnjVuXjJ94tdpTRwW1260911.shtml
- [ ] 规则同意

### 73. `so.com`

- 请求 URL：https://so.com/
- 首次加载最终 URL：https://www.so.com/?src=so.com
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：360搜索；搜全网；问AI；AI写作
- [ ] 规则同意

### 75. `volcengine.com`

- 请求 URL：https://volcengine.com/
- 首次加载最终 URL：https://www.volcengine.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：火山引擎；火山方舟；精选产品；控制台
- [ ] 规则同意

### 78. `nga.cn`

- 请求 URL：https://nga.cn/
- 首次加载最终 URL：https://nga.cn/
- 标签 / 模型：`content_list` / `page_identity`
- 规则理由：帖子身份在 read.php 查询参数中，共享提取器会合并为一个路径；暂按页面身份核验。
- 稳定文字：NGA玩家社区；论坛；评分；专题
- [ ] 规则同意

### 80. `yandex.com`

- 请求 URL：https://yandex.com/
- 首次加载最终 URL：https://yandex.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：Yandex；Search with Yandex AI；Maps
- [ ] 规则同意

### 81. `elsevier.com`

- 请求 URL：https://elsevier.com/
- 首次加载最终 URL：https://www.elsevier.com/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：Elsevier；Academic & Government；Health；Publish with us
- [ ] 规则同意

### 82. `huawei.com`

- 请求 URL：https://huawei.com/
- 首次加载最终 URL：https://www.huawei.com/en/
- 标签 / 模型：`landing_page` / `page_identity`
- 规则理由：按稳定页面身份与主要功能文字验收。
- 稳定文字：Huawei；Enterprise；Consumer；Huawei Cloud
- [ ] 规则同意

## 总体人工确认

- [ ] 同意 40 个新增网站的标签。
- [ ] 同意逐站稳定文字组和具名链接路径契约。
- [ ] 同意动态成员和值不作为固定真值。
- [ ] 确认后再生成合并后的 100 站可执行测试集，并运行 `lexmount-global`。
